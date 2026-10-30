// Send one reading from Go:
//
//	SENSOR_ID=... SECRET=... go run ./examples/send
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"

	hydronode "github.com/TexhFexLabs/hydronode-sdk/go"
)

func main() {
	client := hydronode.New(os.Getenv("SENSOR_ID"), os.Getenv("SECRET"))
	client.Firmware = "my-gateway/1.0.0 linux" // optional: shows in the HydroNode fleet view

	// Commands from HydroNode (send "pump", type UINT32, value 4000 in the app).
	client.On("pump", "UINT32", func(value json.RawMessage) error {
		fmt.Println("pump for", string(value), "ms")
		return nil
	})

	answer, err := client.SendValues(context.Background(), hydronode.Values{
		{Type: "TEMPERATURE", Value: 21.5},
		{Type: "HUMIDITY", Value: 48.2},
	})
	if err != nil {
		fmt.Println("network error:", err)
		os.Exit(1)
	}
	// 202 = stored. 401 = check SENSOR_ID / SECRET or the clock. 429 = too fast.
	fmt.Println(answer.Status, answer.Text)
}
