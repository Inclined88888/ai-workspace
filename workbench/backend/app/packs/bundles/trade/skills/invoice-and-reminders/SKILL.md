---
name: trade-invoice-and-reminders
description: Turn an accepted quote into an invoice, and write polite payment reminder drafts for overdue invoices.
---
# Invoice and payment reminders

Use this when a quote is accepted, when the user asks to invoice a job, or asks who still owes money.

1. trade_list_documents shows quotes and invoices with their status and due dates.
2. trade_make_invoice turns a quote into an invoice with the next invoice number and the user's payment terms.
3. For overdue invoices, trade_reminder_drafts writes three drafts (friendly, firm, final). They are drafts only.
4. Show the draft text so the user can copy it or open it in Mail. The user sends it.
5. Pushing a quote or invoice to ServiceM8 or Xero needs the user's own key on the Trade page and the user's OK each time.
