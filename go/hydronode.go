// Package hydronode sends signed sensor readings to HydroNode and takes commands back.
//
//	client := hydronode.New("sensor-id", "secret")
//	answer, err := client.SendValues(ctx, hydronode.Values{{"TEMPERATURE", 21.5}, {"HUMIDITY", 48}})
//
// It signs every request the way HydroNode checks it (HMAC-SHA256), formats values the way
// HydroNode reads them, and sends one value, one round, or rounds stored earlier with the time
// they were measured. Standard library only.
//
// Protocol: PROTOCOL.md in https://github.com/TexhFexLabs/hydronode-sdk
package hydronode

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Version of this SDK, sent in X-Firmware.
const Version = "1.0.0"

// Limits of the HydroNode server.
const (
	MaxValuesPerRound               = 64
	MaxRoundsPerRequest             = 100
	MaxValuesPerRoundsRequest       = 512
	minValidTime              int64 = 1600000000
)

// ---------------------------------------------------------------------------------------------
// The wire contract. Pure functions: use them with your own HTTP client.
// ---------------------------------------------------------------------------------------------

// FormatValue gives the value with two decimals, as HydroNode reads it: the value as a 32-bit
// float, rounded half up on its exact value (like Java's "%.2f"), minus sign kept also for -0.0.
//
//	FormatValue(21.125) == "21.13", FormatValue(0.005) == "0.00", FormatValue(-0.004) == "-0.00"
func FormatValue(value float64) (string, error) {
	f := float32(value)
	if math.IsNaN(value) || math.IsInf(float64(f), 0) {
		return "", errors.New("hydronode: value must be a finite number that fits a 32-bit float")
	}
	bits := math.Float32bits(f)
	negative := bits>>31 == 1
	exponent := int((bits >> 23) & 0xff)
	fraction := uint64(bits & 0x7fffff)
	mantissa, shift := fraction|0x800000, exponent-150
	if exponent == 0 {
		mantissa, shift = fraction, -149
	}
	cents := new(big.Int).SetUint64(mantissa * 100)
	switch {
	case shift >= 0:
		cents.Lsh(cents, uint(shift))
	case shift < -60:
		cents.SetInt64(0) // far below 0.005
	default:
		den := new(big.Int).Lsh(big.NewInt(1), uint(-shift))
		rem := new(big.Int)
		cents.QuoRem(cents, den, rem)
		if rem.Lsh(rem, 1).Cmp(den) >= 0 {
			cents.Add(cents, big.NewInt(1))
		}
	}
	whole, part := new(big.Int).QuoRem(cents, big.NewInt(100), new(big.Int))
	sign := ""
	if negative {
		sign = "-"
	}
	return fmt.Sprintf("%s%s.%02d", sign, whole.String(), part.Int64()), nil
}

// ValidType reports whether name is A-Z, 0-9 and _, starting with a letter, at most 64 characters.
func ValidType(name string) bool {
	if len(name) < 1 || len(name) > 64 || name[0] < 'A' || name[0] > 'Z' {
		return false
	}
	for i := 0; i < len(name); i++ {
		c := name[i]
		if !(c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_') {
			return false
		}
	}
	return true
}

// Sign returns Base64(HMAC-SHA256(secret, message)): the X-Signature header.
func Sign(secret, message string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(message))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}

// Value is one measurement: type (e.g. "TEMPERATURE") and value.
type Value struct {
	Type  string
	Value float64
}

// Values of one round, in the order they are sent.
type Values []Value

func (vs Values) check() error {
	if len(vs) < 1 || len(vs) > MaxValuesPerRound {
		return errors.New("hydronode: send 1 to 64 values")
	}
	seen := make(map[string]bool, len(vs))
	for _, v := range vs {
		if !ValidType(v.Type) {
			return fmt.Errorf("hydronode: invalid type %q: use A-Z, 0-9 and _ like TEMPERATURE", v.Type)
		}
		if seen[v.Type] {
			return fmt.Errorf("hydronode: type %s appears twice", v.Type)
		}
		seen[v.Type] = true
		if _, err := FormatValue(v.Value); err != nil {
			return err
		}
	}
	return nil
}

func (vs Values) items() string {
	parts := make([]string, len(vs))
	for i, v := range vs {
		text, _ := FormatValue(v.Value)
		parts[i] = `{"type":"` + v.Type + `","value":` + text + `}`
	}
	return strings.Join(parts, ",")
}

// ValuePayload is the canonical text of one value.
func ValuePayload(sensorID, valueType string, value float64, timestamp int64) (string, error) {
	text, err := FormatValue(value)
	if err != nil {
		return "", err
	}
	return `{"sensorId":"` + sensorID + `","type":"` + valueType + `","value":` + text +
		`,"timestamp":` + strconv.FormatInt(timestamp, 10) + `}`, nil
}

// RoundPayload is the canonical text of one round.
func RoundPayload(sensorID string, timestamp int64, values Values) (string, error) {
	if err := values.check(); err != nil {
		return "", err
	}
	return `{"sensorId":"` + sensorID + `","timestamp":` + strconv.FormatInt(timestamp, 10) +
		`,"values":[` + values.items() + `]}`, nil
}

// RoundsEnvelope is the canonical text of a stored-rounds request: send time and round signatures.
func RoundsEnvelope(sensorID string, sent int64, signatures []string) string {
	quoted := make([]string, len(signatures))
	for i, s := range signatures {
		quoted[i] = `"` + s + `"`
	}
	return `{"sensorId":"` + sensorID + `","timestamp":` + strconv.FormatInt(sent, 10) +
		`,"rounds":[` + strings.Join(quoted, ",") + `]}`
}

// ---------------------------------------------------------------------------------------------
// The client.
// ---------------------------------------------------------------------------------------------

// Command is one command from HydroNode, as it arrives in an answer.
type Command struct {
	ID      string          `json:"id"`
	Command string          `json:"command"`
	Type    string          `json:"type,omitempty"`
	Value   json.RawMessage `json:"value"`
}

// Rejected is a round (stored rounds) or a type (live round) HydroNode did not take.
type Rejected struct {
	Round  int    `json:"round"`
	Type   string `json:"type,omitempty"`
	Status int    `json:"status"`
}

// Response is what HydroNode answered. Status 202 = accepted.
type Response struct {
	Status   int
	Text     string
	Accepted int        `json:"accepted"`
	Rounds   int        `json:"rounds"`
	Rejected []Rejected `json:"rejected"`
	Retry    []int      `json:"retry"`
	Commands []Command  `json:"commands"`
}

// OK reports a 2xx answer.
func (r *Response) OK() bool { return r.Status >= 200 && r.Status < 300 }

// Round is one stored round: when it was measured and its values.
type Round struct {
	MeasuredAt int64  `json:"t"`
	Values     Values `json:"v"`
}

// Handler runs a command. Return an error to log it; the command is already confirmed.
type Handler func(value json.RawMessage) error

type handler struct {
	valueType string
	fn        Handler
}

// Client sends signed readings to one HydroNode sensor. Safe for use by several goroutines.
type Client struct {
	SensorID string
	secret   string
	// BaseURL of HydroNode (default https://hydronode.tech).
	BaseURL string
	// HTTP client (default: 15 s timeout).
	HTTP *http.Client
	// Clock returns Unix seconds (default time.Now).
	Clock func() int64
	// Firmware is sent as X-Firmware and shown in the fleet view.
	Firmware string
	// Network is sent as net= (wifi, eth, lte-m, nb-iot, lte, 2g, cellular).
	Network string

	mu       sync.Mutex
	buffer   []Round
	handlers map[string][]handler
}

// New returns a client for one sensor (sensor ID and secret from HydroNode: sensor settings, Connection).
func New(sensorID, secret string) *Client {
	return &Client{
		SensorID: sensorID,
		secret:   secret,
		BaseURL:  "https://hydronode.tech",
		HTTP:     &http.Client{Timeout: 15 * time.Second},
		Clock:    func() int64 { return time.Now().Unix() },
		Firmware: "hydronode-go/" + Version,
		handlers: map[string][]handler{},
	}
}

func (c *Client) now() (int64, error) {
	now := c.Clock()
	if now < minValidTime {
		return 0, errors.New("hydronode: clock not set, HydroNode would reject the signature")
	}
	return now, nil
}

// SendValue sends one value, measured now. Prefer SendValues for several values.
func (c *Client) SendValue(ctx context.Context, valueType string, value float64) (*Response, error) {
	if !ValidType(valueType) {
		return nil, fmt.Errorf("hydronode: invalid type %q", valueType)
	}
	now, err := c.now()
	if err != nil {
		return nil, err
	}
	payload, err := ValuePayload(c.SensorID, valueType, value, now)
	if err != nil {
		return nil, err
	}
	return c.handle(ctx, "/api/webhook/sensor-value", payload, now, Sign(c.secret, payload+strconv.FormatInt(now, 10)))
}

// SendValues sends one round, measured now (or at measuredAt[0], within 2 minutes).
func (c *Client) SendValues(ctx context.Context, values Values, measuredAt ...int64) (*Response, error) {
	ts, err := c.now()
	if err != nil {
		return nil, err
	}
	if len(measuredAt) > 0 {
		ts = measuredAt[0]
	}
	payload, err := RoundPayload(c.SensorID, ts, values)
	if err != nil {
		return nil, err
	}
	return c.handle(ctx, "/api/webhook/sensor-values", payload, ts, Sign(c.secret, payload+strconv.FormatInt(ts, 10)))
}

// SendRounds sends rounds measured earlier, each with its own time (up to 7 days back), oldest first.
// Response.Retry lists the indexes to send again later.
func (c *Client) SendRounds(ctx context.Context, rounds []Round) (*Response, error) {
	if len(rounds) < 1 || len(rounds) > MaxRoundsPerRequest {
		return nil, errors.New("hydronode: send 1 to 100 rounds")
	}
	sent, err := c.now()
	if err != nil {
		return nil, err
	}
	signatures := make([]string, len(rounds))
	parts := make([]string, len(rounds))
	count := 0
	for i, r := range rounds {
		canonical, err := RoundPayload(c.SensorID, r.MeasuredAt, r.Values)
		if err != nil {
			return nil, err
		}
		count += len(r.Values)
		ts := strconv.FormatInt(r.MeasuredAt, 10)
		signatures[i] = Sign(c.secret, canonical+ts)
		parts[i] = `{"timestamp":` + ts + `,"signature":"` + signatures[i] + `","values":[` + r.Values.items() + `]}`
	}
	if count > MaxValuesPerRoundsRequest {
		return nil, errors.New("hydronode: at most 512 values per request")
	}
	body := `{"sensorId":"` + c.SensorID + `","timestamp":` + strconv.FormatInt(sent, 10) + `,"rounds":[` + strings.Join(parts, ",") + `]}`
	envelope := RoundsEnvelope(c.SensorID, sent, signatures)
	return c.handle(ctx, "/api/webhook/sensor-rounds", body, sent, Sign(c.secret, envelope+strconv.FormatInt(sent, 10)))
}

// Record stores one round without sending it (measuredAt 0 = now). Sent by Flush.
func (c *Client) Record(values Values, measuredAt int64) error {
	if err := values.check(); err != nil {
		return err
	}
	if measuredAt == 0 {
		now, err := c.now()
		if err != nil {
			return err
		}
		measuredAt = now
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.buffer = append(c.buffer, Round{MeasuredAt: measuredAt, Values: append(Values(nil), values...)})
	return nil
}

// Buffered returns a copy of the stored rounds (e.g. to save them to a file).
func (c *Client) Buffered() []Round {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]Round(nil), c.buffer...)
}

// Restore replaces the stored rounds (e.g. loaded from a file after a restart).
func (c *Client) Restore(rounds []Round) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.buffer = append([]Round(nil), rounds...)
}

// Flush sends the stored rounds, perRequest at a time (0 = 12), oldest first. Rounds HydroNode
// took or refused for good leave the buffer, rounds it could not take now stay. A network error
// is returned; the rounds stay.
func (c *Client) Flush(ctx context.Context, perRequest int) (*Response, error) {
	if perRequest <= 0 {
		perRequest = 12
	}
	var answer *Response
	for {
		c.mu.Lock()
		n := len(c.buffer)
		if n > perRequest {
			n = perRequest
		}
		chunk := append([]Round(nil), c.buffer[:n]...)
		c.mu.Unlock()
		if n == 0 {
			return answer, nil
		}
		var err error
		answer, err = c.SendRounds(ctx, chunk)
		if err != nil {
			return answer, err
		}
		if answer.Status == http.StatusBadRequest {
			c.replace(n, nil) // broken for good
			continue
		}
		if !answer.OK() {
			return answer, nil
		}
		var keep []Round
		for _, i := range answer.Retry {
			if i >= 0 && i < n {
				keep = append(keep, chunk[i])
			}
		}
		c.replace(n, keep)
		if len(keep) > 0 {
			return answer, nil
		}
	}
}

func (c *Client) replace(n int, keep []Round) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.buffer = append(keep, c.buffer[n:]...)
}

// On runs fn for a command from HydroNode. valueType BOOL, INT32, UINT32, INT64, UINT64 or STRING
// ("" = any); commands of other types are declined with TYPE_MISMATCH.
func (c *Client) On(command, valueType string, fn Handler) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.handlers[command] = append(c.handlers[command], handler{valueType, fn})
}

type declined struct {
	ID     string `json:"id"`
	Reason string `json:"reason"`
}

// Ack confirms command IDs and declines others. Called for you after every send.
func (c *Client) Ack(ctx context.Context, accepted []string, declines []declined) (*Response, error) {
	if accepted == nil {
		accepted = []string{}
	}
	body, _ := json.Marshal(map[string]any{"sensorId": c.SensorID, "commandIds": accepted, "declined": declines})
	now, err := c.now()
	if err != nil {
		return nil, err
	}
	return c.post(ctx, "/api/webhook/sensor-command-ack", string(body), now, Sign(c.secret, string(body)+strconv.FormatInt(now, 10)))
}

func fits(valueType string, raw json.RawMessage) bool {
	text := strings.TrimSpace(string(raw))
	switch valueType {
	case "BOOL":
		return text == "true" || text == "false"
	case "STRING":
		return strings.HasPrefix(text, `"`)
	case "INT32":
		_, err := strconv.ParseInt(text, 10, 32)
		return err == nil
	case "UINT32":
		_, err := strconv.ParseUint(text, 10, 32)
		return err == nil
	case "INT64":
		_, err := strconv.ParseInt(text, 10, 64)
		return err == nil
	case "UINT64":
		_, err := strconv.ParseUint(text, 10, 64)
		return err == nil
	}
	return false
}

func (c *Client) pick(cmd Command) (Handler, string) {
	c.mu.Lock()
	list := c.handlers[cmd.Command]
	c.mu.Unlock()
	if len(list) == 0 {
		return nil, "NO_HANDLER"
	}
	for _, h := range list {
		if h.valueType == "" || cmd.Type == "" || h.valueType == cmd.Type {
			if h.valueType != "" && !fits(h.valueType, cmd.Value) {
				if cmd.Type != "" {
					return nil, "INVALID_VALUE"
				}
				return nil, "TYPE_MISMATCH"
			}
			return h.fn, ""
		}
	}
	return nil, "TYPE_MISMATCH"
}

// handle posts, answers the commands of the response (before running them), then runs them.
func (c *Client) handle(ctx context.Context, path, body string, ts int64, signature string) (*Response, error) {
	answer, err := c.post(ctx, path, body, ts, signature)
	if err != nil || !answer.OK() || len(answer.Commands) == 0 {
		return answer, err
	}
	var accepted []string
	var declines []declined
	type job struct {
		fn    Handler
		value json.RawMessage
	}
	var run []job
	for _, cmd := range answer.Commands {
		fn, reason := c.pick(cmd)
		if fn != nil {
			accepted = append(accepted, cmd.ID)
			run = append(run, job{fn, cmd.Value})
		} else {
			declines = append(declines, declined{cmd.ID, reason})
		}
	}
	if _, err := c.Ack(ctx, accepted, declines); err != nil {
		return answer, err
	}
	for _, j := range run {
		_ = j.fn(j.value)
	}
	return answer, nil
}

func (c *Client) post(ctx context.Context, path, body string, ts int64, signature string) (*Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(c.BaseURL, "/")+path, bytes.NewBufferString(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Sensor-Id", c.SensorID)
	req.Header.Set("X-Timestamp", strconv.FormatInt(ts, 10))
	req.Header.Set("X-Signature", signature)
	req.Header.Set("X-Firmware", c.Firmware)
	if c.Network != "" {
		req.Header.Set("X-Device-Status", "net="+c.Network)
	}
	reply, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer reply.Body.Close()
	data, err := io.ReadAll(io.LimitReader(reply.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	answer := &Response{Status: reply.StatusCode, Text: string(data)}
	if bytes.HasPrefix(data, []byte("{")) {
		_ = json.Unmarshal(data, answer)
	}
	return answer, nil
}
