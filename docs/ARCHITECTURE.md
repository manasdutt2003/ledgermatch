# Architecture

`ledgermatch` is a small pipeline: read two CSV files, match them in passes, then report with a check that the totals balance.

## Modules

| Module | Responsibility |
| --- | --- |
| `parse.py` | Reads messy CSVs into `Txn` records. Amounts become integer cents; a date column is parsed with one consistent format; columns are found by heading synonyms or an explicit mapping. Problems raise `ParseError`, which names the file and row. |
| `match.py` | The matching engine: `Config`, `Match`, `Result` and the passes described below. |
| `report.py` | `summarise` computes totals and the balancing check. `write` produces `report.md`, `matches.csv`, `unmatched_bank.csv` and `unmatched_ledger.csv`. |
| `bench.py` | A generator of synthetic months with known true links, a naive same-amount/nearest-date baseline, and precision/recall scoring. |
| `cli.py` | Argument parsing, environment defaults, logging setup, and the `reconcile`, `demo` and `bench` commands. |

## Data flow

```
bank.csv  ──┐                                   ┌─ report.md
            ├─ parse.load_csv ─ match.reconcile ┤─ matches.csv
ledger.csv ─┘   (Txn lists)     (Result)        └─ unmatched_*.csv
                                    │
                          report.summarise (balance check)
```

Sign convention after loading: positive is money in, negative is money out. A cash book's debit is treated as a receipt, and the CLI flips ledger signs if they look opposite to the bank's.

## Matching passes

Each pass removes what it matched before the next one runs (see the docstring at the top of `match.py`):

1. **One to one**: same amount and dates inside the window. For each amount, the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) picks the pairing with the highest total score. The score combines date closeness, description overlap and shared reference numbers.
2. **Batch**: one bank line equal to the sum of several ledger entries, found by a depth-first subset search with pruning and a step budget.
3. **Split**: the same search in the other direction, limited to bank lines that name the ledger entry's payee.
4. **Fee**: receipts that arrive short by up to a configured percentage plus a fixed amount, with a matching payee.
5. **Suggestions**: likely keying errors are reported for a person and never counted as matched.

A group is accepted only when the search was exhaustive and one combination is the only or clearly best answer. Otherwise it is left unmatched and a note is added to the result.

## The balancing check

Every run verifies:

```
bank total - ledger total = unmatched bank - unmatched ledger + fee differences on matched receipts
```

Because amounts are integer cents the comparison is exact. `reconcile` exits with status 0 only if it holds (status 3 otherwise); exit status 2 means an input problem.

## Configuration

| Setting | Flag | Environment variable | Default |
| --- | --- | --- | --- |
| Output folder | `-o/--out` | `LEDGERMATCH_OUT` | `reconciliation` |
| Days a bank line may precede the ledger entry | `--early-days` | `LEDGERMATCH_EARLY_DAYS` | 3 |
| Days a bank line may follow the ledger entry | `--late-days` | `LEDGERMATCH_LATE_DAYS` | 10 |
| Log level on stderr | `-v/--verbose` (INFO) | `LEDGERMATCH_LOG_LEVEL` | `WARNING` |

Flags override environment variables. Other matching thresholds live in `match.Config` and are not exposed on the command line.

Logs go to stderr only, so the summary on stdout is unchanged.

## Running in Docker

```bash
docker build -t ledgermatch .
docker run --rm -v "$PWD:/data" ledgermatch reconcile bank.csv ledger.csv -o out
```

The container's working directory is `/data`, so relative paths refer to the mounted folder.

## Design trade-offs

- **Declining to guess.** Ambiguous groups are left for a human instead of picking one. This costs recall at high volume (see the README's volume table) but protects precision.
- **Heuristic scoring.** The weights and thresholds in `match.py` were tuned on generated data, which the README states as a limitation. They have not been validated on real company records.
- **Exact arithmetic.** Cents as integers avoid floating-point drift in sums and make the balance check meaningful.
