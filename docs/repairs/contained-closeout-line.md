# A close-out line can accompany its evidence

`msg-to-watchdog` now accepts its expected text as an exact, complete line in a
longer message. A substring in the middle of a sentence still does not qualify.
Recipient checks, successful post-delivery calls and actual receiver evidence
remain required. The full received body must still match the successful call's
message: independently finding the same line in two different bodies is not a
corresponding send and delivery.

The real-handler control failed on `1b769cd`:

```
FAIL: test_closeout_line_with_detail_settles (__main__.AcceptanceContract)
Ran 57 tests in 3.731s
FAILED (failures=1)
```

Its paired mid-sentence negative passes both before and after the repair. The
final acceptance script passes all 57 controls. Read-only evaluation of the real
engine-test item now returns PASS from its actual matching delivered close-out;
no item was moved or edited in live state.
