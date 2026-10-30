package hydronode

import (
	"context"
	"encoding/json"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
)

type vectors struct {
	Secret   string `json:"secret"`
	SensorID string `json:"sensorId"`
	Values   []struct {
		Input   float64 `json:"input"`
		Float32 string  `json:"float32"`
		Text    string  `json:"text"`
	} `json:"values"`
	Single []struct {
		Name, Type, Canonical, Signature string
		Value                            float64
		Timestamp                        int64
	} `json:"single"`
	Round  []roundVector `json:"round"`
	Rounds []struct {
		Name      string        `json:"name"`
		Sent      int64         `json:"sent"`
		Rounds    []roundVector `json:"rounds"`
		Envelope  string        `json:"envelope"`
		Signature string        `json:"signature"`
	} `json:"rounds"`
	Raw []struct {
		Name, Payload, Signature string
		Timestamp                int64
	} `json:"raw"`
}

type roundVector struct {
	Timestamp int64 `json:"timestamp"`
	Values    []struct {
		Type  string  `json:"type"`
		Value float64 `json:"value"`
	} `json:"values"`
	Canonical string `json:"canonical"`
	Signature string `json:"signature"`
}

func load(t *testing.T) vectors {
	data, err := os.ReadFile("../vectors/signature-vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var v vectors
	if err := json.Unmarshal(data, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func TestSharedVectors(t *testing.T) {
	v := load(t)
	for _, c := range v.Values {
		bits, _ := strconv.ParseUint(strings.TrimPrefix(c.Float32, "0x"), 16, 32)
		got, err := FormatValue(float64(math.Float32frombits(uint32(bits))))
		if err != nil || got != c.Text {
			t.Errorf("value %v: got %q, want %q (%v)", c.Input, got, c.Text, err)
		}
	}
	for _, c := range v.Single {
		canonical, _ := ValuePayload(v.SensorID, c.Type, c.Value, c.Timestamp)
		if canonical != c.Canonical {
			t.Errorf("single %s: %s", c.Name, canonical)
		}
		if Sign(v.Secret, canonical+strconv.FormatInt(c.Timestamp, 10)) != c.Signature {
			t.Errorf("single signature %s", c.Name)
		}
	}
	checkRound := func(r roundVector) {
		values := Values{}
		for _, x := range r.Values {
			values = append(values, Value{x.Type, x.Value})
		}
		canonical, err := RoundPayload(v.SensorID, r.Timestamp, values)
		if err != nil || canonical != r.Canonical {
			t.Errorf("round %d: %s (%v)", r.Timestamp, canonical, err)
		}
		if Sign(v.Secret, canonical+strconv.FormatInt(r.Timestamp, 10)) != r.Signature {
			t.Errorf("round signature %d", r.Timestamp)
		}
	}
	for _, r := range v.Round {
		checkRound(r)
	}
	for _, c := range v.Rounds {
		var sigs []string
		for _, r := range c.Rounds {
			checkRound(r)
			sigs = append(sigs, r.Signature)
		}
		envelope := RoundsEnvelope(v.SensorID, c.Sent, sigs)
		if envelope != c.Envelope || Sign(v.Secret, envelope+strconv.FormatInt(c.Sent, 10)) != c.Signature {
			t.Errorf("envelope %s: %s", c.Name, envelope)
		}
	}
	for _, c := range v.Raw {
		if Sign(v.Secret, c.Payload+strconv.FormatInt(c.Timestamp, 10)) != c.Signature {
			t.Errorf("raw %s", c.Name)
		}
	}
}

func TestRules(t *testing.T) {
	for _, bad := range []string{"temperature", "1TEMP", "PM2.5", "", strings.Repeat("A", 65)} {
		if ValidType(bad) {
			t.Errorf("%q accepted", bad)
		}
	}
	if _, err := FormatValue(math.NaN()); err == nil {
		t.Error("NaN accepted")
	}
	if _, err := RoundPayload("s", 1, Values{{"T", 1}, {"T", 2}}); err == nil {
		t.Error("duplicate type accepted")
	}
}

const now = 1730642400

// fakeServer answers like HydroNode and checks every signature.
func fakeServer(t *testing.T, answers ...string) (*httptest.Server, *[]string, *[]string) {
	var paths, bodies []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		body := string(data)
		paths = append(paths, r.URL.Path)
		bodies = append(bodies, body)
		message := body
		if r.URL.Path == "/api/webhook/sensor-rounds" {
			var req struct {
				SensorID  string `json:"sensorId"`
				Timestamp int64  `json:"timestamp"`
				Rounds    []struct {
					Signature string `json:"signature"`
				} `json:"rounds"`
			}
			_ = json.Unmarshal(data, &req)
			var sigs []string
			for _, x := range req.Rounds {
				sigs = append(sigs, x.Signature)
			}
			message = RoundsEnvelope(req.SensorID, req.Timestamp, sigs)
		}
		if Sign("secret", message+r.Header.Get("X-Timestamp")) != r.Header.Get("X-Signature") {
			w.WriteHeader(401)
			_, _ = w.Write([]byte("Invalid signature"))
			return
		}
		answer := `{"accepted":1,"commands":[]}`
		if len(answers) > 0 {
			answer, answers = answers[0], answers[1:]
		}
		w.WriteHeader(202)
		_, _ = w.Write([]byte(answer))
	}))
	t.Cleanup(srv.Close)
	return srv, &paths, &bodies
}

func client(srv *httptest.Server) *Client {
	c := New("sensor-1", "secret")
	c.BaseURL = srv.URL
	c.Clock = func() int64 { return now }
	return c
}

func TestSendValuesSignsTheBody(t *testing.T) {
	srv, paths, bodies := fakeServer(t)
	answer, err := client(srv).SendValues(context.Background(), Values{{"TEMPERATURE", 21.125}, {"HUMIDITY", 48}})
	if err != nil || answer.Status != 202 {
		t.Fatalf("%v %v", answer, err)
	}
	want := `{"sensorId":"sensor-1","timestamp":1730642400,"values":[{"type":"TEMPERATURE","value":21.13},{"type":"HUMIDITY","value":48.00}]}`
	if (*paths)[0] != "/api/webhook/sensor-values" || (*bodies)[0] != want {
		t.Errorf("%s %s", (*paths)[0], (*bodies)[0])
	}
}

func TestCommandsAreAckedBeforeTheyRun(t *testing.T) {
	srv, paths, bodies := fakeServer(t, `{"accepted":1,"commands":[
		{"id":"c1","command":"pump","type":"UINT32","value":4000},
		{"id":"c2","command":"lamp","type":"UINT32","value":1},
		{"id":"c3","command":"fan","type":"BOOL","value":true},
		{"id":"c4","command":"pump","type":"UINT32","value":-1}]}`)
	c := client(srv)
	var ran []string
	c.On("pump", "UINT32", func(v json.RawMessage) error {
		ran = append(ran, "pump "+string(v)+" after "+strconv.Itoa(len(*paths))+" requests")
		return nil
	})
	c.On("lamp", "BOOL", func(json.RawMessage) error { ran = append(ran, "lamp"); return nil })
	if _, err := c.SendValues(context.Background(), Values{{"TEMPERATURE", 20}}); err != nil {
		t.Fatal(err)
	}
	if (*paths)[1] != "/api/webhook/sensor-command-ack" {
		t.Fatalf("no ack: %v", *paths)
	}
	var ack struct {
		CommandIDs []string   `json:"commandIds"`
		Declined   []declined `json:"declined"`
	}
	_ = json.Unmarshal([]byte((*bodies)[1]), &ack)
	reasons := map[string]string{}
	for _, d := range ack.Declined {
		reasons[d.ID] = d.Reason
	}
	if len(ack.CommandIDs) != 1 || ack.CommandIDs[0] != "c1" || reasons["c2"] != "TYPE_MISMATCH" ||
		reasons["c3"] != "NO_HANDLER" || reasons["c4"] != "INVALID_VALUE" {
		t.Errorf("ack %+v", ack)
	}
	if len(ran) != 1 || ran[0] != "pump 4000 after 2 requests" {
		t.Errorf("ran %v", ran)
	}
}

func TestFlushKeepsWhatShouldBeRetried(t *testing.T) {
	srv, _, bodies := fakeServer(t, `{"accepted":3,"rounds":3,"rejected":[{"round":1,"status":429}],"retry":[1],"commands":[]}`)
	c := client(srv)
	for i := int64(0); i < 5; i++ {
		_ = c.Record(Values{{"TEMPERATURE", float64(20 + i)}}, now-3600+i*600)
	}
	if _, err := c.Flush(context.Background(), 4); err != nil {
		t.Fatal(err)
	}
	if left := c.Buffered(); len(left) != 2 || left[0].MeasuredAt != now-3000 {
		t.Errorf("left %+v", left)
	}
	if !strings.Contains((*bodies)[0], `"rounds":[{"timestamp":1730638800,`) {
		t.Errorf("first body %s", (*bodies)[0])
	}
	if _, err := c.Flush(context.Background(), 0); err != nil || len(c.Buffered()) != 0 {
		t.Errorf("rest not sent: %v", err)
	}
}

func TestUnsetClock(t *testing.T) {
	c := New("s", "k")
	c.Clock = func() int64 { return 12 }
	if _, err := c.SendValues(context.Background(), Values{{"T", 1}}); err == nil {
		t.Error("unset clock accepted")
	}
}
