// Arrow-function exports — exercise the `=>`-in-documentation heuristic in
// orgraph/extract/scip.py that turns `export const fn = () => {}` into a
// Function node. Plain non-function consts must remain unlabeled.

export const formatName = (s: string): string => s.trim().toLowerCase();

export const sum = (a: number, b: number): number => a + b;

// Plain value export — must NOT become a Function node.
export const VERSION = "1.0.0";

export function logName(name: string): void {
  const formatted = formatName(name);
  console.log(formatted);
}
