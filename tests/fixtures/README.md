# Real captured payloads

`04-core-loop-implementation.md` §9 asks for 2–3 **real** failed-payment webhook
bodies to be captured here, so the rule patterns in `app/config.py` are grounded in
actual Razorpay payloads rather than guessed ones.

How to capture (Step 1 of `08-build-plan.md`):

1. Run the app and the tunnel: `uvicorn app.main:app --port 8000` and `ngrok http 8000`.
2. Force a failed subscription charge (`02-razorpay-setup.md` §4) with a failure test
   card (§5).
3. Open the ngrok inspector at http://127.0.0.1:4040, find the delivery, and save the
   raw request body verbatim as `payment_failed_<reason>.json`.
4. Record the card you used in `docs/notes-test-cards.md`.
5. Add the fixture to `tests/test_diagnosis_rules.py` so the rule it should match is
   asserted against the real body.

This directory is **empty on purpose**: no Razorpay account existed in the environment
this repo was built in, and inventing a "real" payload here would defeat the point.
