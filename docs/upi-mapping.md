# Mapping the Intent Mandate onto UPI Reserve Pay

Parchi's slip is a Google **AP2 Intent Mandate**, which is written for card-shaped
rails. India's agent-payment rail is UPI, and Razorpay's agentic products sit on
top of it. This page maps one onto the other, field by field, so the design is not
US-card thinking transplanted into an Indian pitch.

## Why Reserve Pay is the right anchor

UPI Reserve Pay (block-and-debit) already separates two moments that card rails
blur together: the customer **blocks** an amount in their own account ahead of
time, and the merchant **debits** against that block later. That is the
shape of agent spending: the human authorises before the purchase exists, and the
agent transacts afterwards without the human present.

What Reserve Pay gives you is a *ceiling and a window*. What it does not give you
is a *purpose*: the block does not record what the money was approved for, so
nothing on the rail can tell an in-scope purchase from an out-of-scope one under
the same ceiling. Parchi's mandate carries the purpose, and the checkpoint is
where the two meet.

## Field by field

| Intent Mandate field | UPI Reserve Pay equivalent | Notes |
| --- | --- | --- |
| `payer_id` | Payer VPA / account handle on the mandate | One-to-one. |
| `payee_id` | Payee VPA / merchant ID on the block | Reserve Pay blocks are payee-scoped; the mandate must not out-live the block's payee. |
| `max_amount_paise` | Blocked amount | The rail already enforces this ceiling. Parchi checks it too, because the merchant must be able to explain the refusal, not just observe it. |
| `allowed_methods` | Rail selection (UPI vs card) | Reserve Pay narrows this to UPI; the field stays because the same mandate can also be presented on a card rail. |
| `expires_at` | Block validity / mandate end date | AP2 guidance is ~24h. Reserve Pay blocks routinely run longer, so the mandate TTL is the tighter of the two and Parchi enforces the tighter one. |
| `nonce` | UMN + debit sequence number | UPI's Unique Mandate Number identifies the mandate; the nonce makes a single *authorisation* one-time. A recurring UMN would carry a fresh nonce per debit. |
| `signature` | Payer's UPI PIN authorisation of the block | Not equivalent, and this is the interesting gap, see below. |
| `allowed_categories` | **no equivalent** | The rail has no notion of what the money is for. |
| `prompt_playback` | **no equivalent** | The rail has no notion of what the human asked for. |

## The two rows with no equivalent are the product

`allowed_categories` and `prompt_playback` are the fields UPI cannot express, and
they are the two Parchi actually spends its intelligence on. A Reserve Pay block
answers "is there money, and is this the right merchant". It cannot answer "is
this the thing the human asked for", because that sentence was never captured
anywhere on the rail. Parchi captures it at approval time, signs it, and checks
against it at authorisation time.

## Where the signature differs

A UPI PIN authorises the *block* to the issuer; it is not a payer-held key that a
merchant can verify independently. Parchi's Ed25519 signature is verifiable by
anyone holding the payer's public key, which is what makes the evidence pack
useful in a dispute: the merchant can prove what the human approved without
asking the issuer to vouch for it.

In a real integration these coexist: the PIN authorises the block on the rail, and
the mandate signature (from the payer's wallet or agent-provisioning key) travels
with the purchase. Reconciling the two properly needs a key-provisioning story
with the PSP, which is named in the README's limitations rather than pretended
away here.

## Where Parchi would sit in a Razorpay flow

```
human approves in app        agent shops                merchant server
        │                         │                            │
        ├─ UPI Reserve Pay block ─┤                            │
        └─ signed Intent Mandate ─┴──── cart + mandate ───────► Parchi
                                                                 │
                                            ALLOW / STEP_UP ─────┤──► Razorpay Orders/Payments
                                                    BLOCK ───────┘    (debit against the block)
                                                                 │
                                                       hash-chained ledger
                                                    (evidence pack on dispute)
```

Parchi is a pre-authorisation checkpoint, not a payment processor. It returns a
verdict and a receipt; the debit against the block is still Razorpay's.
