# ledgermatch

Reconciles a bank statement against a company's ledger and explains every cent of the
difference between them.

Bank reconciliation is the month-end job of proving that the books and the bank agree.
It is slow by hand because the two records describe the same money differently: the bank
clears a payment days later, shortens the payee's name, lumps a day's receipts into one
deposit, takes a card fee off the top, and adds charges the books have not seen yet. A
spreadsheet lookup on amount and date gets through the easy lines and leaves the rest.

`ledgermatch` handles those cases, says why each leftover line is a leftover, and refuses
to guess when a line could be explained more than one way.

```
$ ledgermatch reconcile bank.csv ledger.csv
Bank lines:         98   matched 91, unmatched 7
Ledger entries:    121   matched 112, unmatched 9
  batch       12
  fee         7
  one_to_one  57
  split       7
  4 match(es) flagged ambiguous
  2 suggested correction(s) for likely keying errors
Difference between bank and ledger: 21,741.48
Explained by the unmatched items and fees: 21,741.48  (balanced)
Report written to reconciliation/report.md
```

## What it handles

| Situation | Example | What the tool does |
| --- | --- | --- |
| Same amount many times | Twelve customers each pay 499.00 | Pairs them by payee name and reference, choosing the best pairing overall |
| Shortened bank descriptions | `NEFT CR EVERGREEN PHAR 9912` | Matches to "Evergreen Pharma Pvt Ltd", ignoring payment jargon |
| Batch deposit | One 1,750.50 deposit for three receipts | Finds the receipts that add up to it |
| Split payment | A 750.00 bill paid as 400.00 and 350.00 | Finds the instalments, which must name the same payee |
| Processing fee | 1,000.00 invoiced, 970.70 received | Matches net of the fee and reports the fee |
| Keying error | 4,979.07 in the books, 4,997.07 at the bank | Suggests the correction; never counts it as matched |
| Duplicate entry | The same payment entered twice | Flags the spare as a possible duplicate |
| Uncleared items | A cheque written on the 27th | Marks it as probably not cleared yet |
| Bank-only lines | Service charges, interest | Marks them as not yet in the books |

It also reads the files as they come: amounts such as `1,234.50`, `(120.00)`, `INR 200`,
`300.00 DR` or `1.234,50`; separate debit and credit columns; and day-first or month-first
dates, decided once per column.

## Install

Requires Python 3.11 or newer.

```bash
git clone https://github.com/manasdutt2003/ledgermatch
cd ledgermatch
pip install -e .
```

## Try it

A generated month of data is included, written in two different messy layouts.

```bash
cd examples/october
ledgermatch reconcile bank.csv ledger.csv -o out
```

This writes four files to `out/`:

| File | Contents |
| --- | --- |
| `report.md` | The reconciliation statement, every unmatched line with its likely reason, and suggested corrections |
| `matches.csv` | Every match, with its type, score and reason |
| `unmatched_bank.csv` | Bank lines with no ledger entry |
| `unmatched_ledger.csv` | Ledger entries with no bank line |

The [example report](examples/october/out/report.md) is in the repository. Its statement:

| | Amount |
| --- | ---: |
| Net movement per bank statement | 155,819.49 |
| Net movement per ledger | 134,078.01 |
| **Difference to explain** | **21,741.48** |
| Bank lines not in the ledger | -919.07 |
| Less: ledger entries not on the statement | 23,054.15 |
| Processing fees on matched receipts | -393.60 |
| **Difference explained** | **21,741.48** |

The command exits with status 0 only when the two figures agree.

### Your own files

```bash
ledgermatch reconcile statement.csv cashbook.csv -o october
```

Columns are found by their headings (`Date`, `Value Date`, `Amount`, `Withdrawal`,
`Deposit`, `Narration`, `Particulars`, `Reference` and similar). If yours are named
differently, say so:

```bash
ledgermatch reconcile statement.csv cashbook.csv \
  --bank-col date="Posted On" --bank-col desc="Payee" --ledger-col amount="Net"
```

Other options: `--dayfirst` or `--monthfirst` for ambiguous dates, `--flip-ledger` if the
ledger records receipts as negative (this is detected automatically when it is obvious),
and `--early-days` and `--late-days` to change how far apart the two dates may be.

## How it works

Amounts are held as whole cents, so sums are exact. Matching runs in passes, and each
pass removes what it matches before the next one starts.

1. **One to one.** Lines with the same amount and dates within the window are scored on
   date gap, shared words in the description, and shared reference numbers. For each
   amount, the pairing with the highest total score is chosen with the Hungarian
   algorithm, so one good match cannot steal a line that a better match needed. A match is
   flagged ambiguous when another candidate scored almost as well.
2. **Batch.** For each remaining bank line, a depth-first search with pruning looks for
   ledger entries from the previous few days that add up to it.
3. **Split.** The same search in the other direction, restricted to bank lines that name
   the ledger entry's payee.
4. **Keying errors.** Pairs with matching payee or reference whose amounts differ by two
   swapped digits, one wrong digit or a shifted decimal point are set aside as suggestions.
5. **Fees.** Receipts that arrived short by up to 3.5% plus 0.50, with a matching payee.

A group is accepted only if the search was exhaustive and exactly one combination adds
up, or one is clearly better than the rest. When a line could be made up in several
ways, it is left unmatched and listed in the report. Accepting one group removes its
entries from the pool, which often leaves another group with only one answer, so the
group pass repeats until nothing changes.

The report rests on one identity, checked on every run:

```
bank total - ledger total = unmatched bank lines - unmatched ledger entries + fees on matched receipts
```

## How accurate is it?

`ledgermatch bench` generates months of bank and ledger activity where the right answer
is known, then scores the engine against it. The comparison is a plain lookup: same
amount, nearest date.

- **Precision** is the share of links the tool asserts that are right.
- **Recall** is the share of true links it finds.

Thirty generated months of about 400 ledger entries each, at three difficulty levels
(harder means fewer reference numbers on the bank side, more repeated amounts, longer
clearing delays and shorter names):

| Difficulty | Engine precision | Engine recall | Lookup precision | Lookup recall | Keying errors caught |
| --- | ---: | ---: | ---: | ---: | ---: |
| Easy | 100.0% | 98.5% | 95.8% | 51.5% | 288 of 296 |
| Medium | 99.5% | 92.9% | 79.4% | 42.5% | 293 of 298 |
| Hard | 96.6% | 84.8% | 59.5% | 31.9% | 279 of 283 |

Share of true links found, by situation:

| Difficulty | One to one | Batch | Split | Fee |
| --- | ---: | ---: | ---: | ---: |
| Easy | 100.0% | 94.8% | 100.0% | 99.4% |
| Medium | 99.4% | 76.2% | 100.0% | 98.0% |
| Hard | 95.3% | 54.0% | 100.0% | 98.1% |

Reproduce with `ledgermatch bench --seeds 30 --first-seed 1001`. The engine's settings
were chosen on seeds 1 to 10; these figures are from months it was not adjusted on.

### It gets worse as volume rises

The same test with more activity packed into one month, at medium difficulty:

| Ledger entries in the month | Engine precision | Engine recall | Batch deposits resolved |
| ---: | ---: | ---: | ---: |
| 400 | 99.5% | 92.9% | 76.2% |
| 2,000 | 97.8% | 69.9% | 2.3% |
| 5,000 | 95.0% | 67.3% | 0.0% |

The 2,000 and 5,000 rows are from five generated months each
(`ledgermatch bench --seeds 5 --first-seed 2001 --entries 5000`). A 5,000-entry month
takes a few seconds to reconcile.

At 5,000 entries a month, dozens of receipts fall inside any deposit's window, so many
combinations add up to the same total and the tool declines to pick one. One-to-one
matching holds up (96.1% of those links found at 5,000). At hard difficulty and 5,000
entries, precision falls to 77.2%: so many lines share an amount and carry no reference
that even the best pairing is often wrong.

## Limitations

- **The accuracy figures come from generated data.** The generator models how statements
  differ from books, and the same person wrote it and the engine, so it tests the
  situations that were thought of. It has not been run on a real company's records, and
  real data will have cases it does not cover.
- **High volume defeats group matching**, as shown above. Resolving batch deposits at
  that scale needs information the statement does not carry, such as the deposit slip.
- **One currency, one account, one period.** There is no foreign-exchange handling and
  no carrying of uncleared items into the next month.
- **A batch that also had a fee deducted is not matched**, for example a card settlement
  covering many sales net of charges.
- **Fee matching uses a fixed ceiling** (3.5% plus 0.50) and a matching payee. It does
  not know any processor's actual rates.
- **Suggestions and likely reasons are heuristics.** They are there to point a person at
  the right line, and each needs checking.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

The tests include a check, over 24 generated months, that every cent of the difference
is explained, that nothing is matched twice, and that only fee matches differ in amount.

## License

MIT
