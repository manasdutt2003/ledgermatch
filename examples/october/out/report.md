# Bank reconciliation

- Bank lines: 98, of which 91 matched and 7 unmatched
- Ledger entries: 121, of which 112 matched and 9 unmatched
- Matches flagged as ambiguous (another candidate scored almost as well): 4

## Matches by type

| Type | Matches |
| --- | ---: |
| One bank line to several ledger entries | 12 |
| Matched net of a processing fee | 7 |
| One bank line to one ledger entry | 57 |
| Several bank lines to one ledger entry | 7 |

## Reconciliation statement

| | Amount |
| --- | ---: |
| Net movement per bank statement | 155,819.49 |
| Net movement per ledger | 134,078.01 |
| **Difference to explain** | **21,741.48** |
| Bank lines not in the ledger | -919.07 |
| Less: ledger entries not on the statement | 23,054.15 |
| Processing fees on matched receipts | -393.60 |
| **Difference explained** | **21,741.48** |

Every cent of the difference is accounted for by the items listed below.

## Bank lines not matched (7)

| ID | Date | Amount | Description | Likely reason |
| --- | --- | ---: | --- | --- |
| B00017 | 2026-10-03 | 287.29 | INTEREST CREDIT | bank charge or interest not yet recorded in the ledger |
| B00029 | 2026-10-03 | -64.18 | ATM CASH WITHDRAWAL | on the statement but not in the ledger |
| B00016 | 2026-10-05 | -232.77 | NEFT CHARGES | bank charge or interest not yet recorded in the ledger |
| B00049 | 2026-10-08 | 4,997.07 | UPI/PIONEER LOGIST INV20284 | amount differs from a similar ledger entry (see suggestions) |
| B00047 | 2026-10-13 | -5,103.92 | NEFT DR PIONEER ELECTR BILL20249 | amount differs from a similar ledger entry (see suggestions) |
| B00014 | 2026-10-15 | -436.43 | MONTHLY SERVICE CHARGE | bank charge or interest not yet recorded in the ledger |
| B00013 | 2026-10-24 | -366.13 | ATM CASH WITHDRAWAL | on the statement but not in the ledger |

## Ledger entries not matched (9)

| ID | Date | Amount | Description | Likely reason |
| --- | --- | ---: | --- | --- |
| L00070 | 2026-10-01 | -1,139.71 | Payment to Apex Exports Pvt Ltd | possible duplicate of L00069, which is already matched |
| L00008 | 2026-10-03 | 49.00 | Receipt from Delta Exports Pvt Ltd | possible duplicate of L00007, which is already matched |
| L00049 | 2026-10-08 | -5,103.29 | Payment to Pioneer Electricals Pvt Ltd | amount differs from a similar bank line (see suggestions) |
| L00055 | 2026-10-08 | 4,979.07 | Receipt from Pioneer Logistics Pvt Ltd | amount differs from a similar bank line (see suggestions) |
| L00041 | 2026-10-14 | -99.00 | Payment to Willow Plastics Pvt Ltd | possible duplicate of L00040, which is already matched |
| L00003 | 2026-10-27 | -4,383.75 | Payment to Nimbus Traders Pvt Ltd | possible duplicate of L00002, which is already matched |
| L00110 | 2026-10-27 | -7,706.53 | Payment to Willow Motors Pvt Ltd | recent entry; probably not cleared by the statement date |
| L00027 | 2026-10-28 | -2,659.44 | Payment to Evergreen Plastics Pvt Ltd | recent entry; probably not cleared by the statement date |
| L00118 | 2026-10-28 | -6,990.50 | Payment to Zenith Builders Pvt Ltd | recent entry; probably not cleared by the statement date |

## Suggested corrections (2)

These are not counted as matched. Each needs a person to confirm and correct the entry.

- B00049 and L00055: likely the same transaction with two adjacent digits swapped: bank 4,997.07, ledger 4,979.07
- B00047 and L00049: likely the same transaction with two adjacent digits swapped: bank -5,103.92, ledger -5,103.29
