---
name: trade-site-quote
description: Turn a site photo, voice note or short description of a job into an itemised quote with labour, materials, markup and tax.
---
# Quote a job from a photo, voice note or description

Use this when the user describes a job, shares a site photo or a voice note, and wants a price.

1. If there is a photo, look at it with describe_image and note what work it shows (sizes, materials, access, damage).
2. Ask at most one short question if something that changes the price is missing (for example the size of the area).
3. Call trade_draft_quote with the description (and the photo file id). It returns labour and materials lines priced with the
   user's own rates, markup and tax settings from the Trade pack page.
4. Show the lines in a short table with the total including tax. Point out any line that is a guess and should be checked.
5. Offer the next steps: edit the lines on the Trade page, make the PDF, or turn it into an invoice.

Rules:
- Prices come from the user's settings and what they say. When you estimate a quantity or cost, say it is an estimate.
- Tax follows the user's settings (Australian GST at 10% by default). Never call something a tax invoice unless the business
  has an ABN and is registered for GST in the settings.
- Nothing is sent to a customer or another system from this skill. Sending is the user's own step.
