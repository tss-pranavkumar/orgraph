package main

// Exercise both single-line and import-block forms so the regex-extension
// (Change 2 in 0.1.33) is covered too.
import "fmt"

import (
	"strings"

	"example.com/b"
)

func Shout(name string) string {
	return strings.ToUpper(b.Greet(name))
}

func main() {
	fmt.Println(Shout("world"))
}
