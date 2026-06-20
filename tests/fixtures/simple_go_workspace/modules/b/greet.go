package b

// Greet builds a greeting for a name — imported by module a.
func Greet(name string) string {
	return "hello " + name
}
