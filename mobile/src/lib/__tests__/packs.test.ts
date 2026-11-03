import { GRADES, docStatus, money, shareText } from '../packs';

describe('packs', () => {
  it('has the four review grades in order', () => {
    expect(GRADES.map((g) => g.grade)).toEqual(['again', 'hard', 'good', 'easy']);
  });
  it('formats Australian dollars', () => {
    expect(money(1617)).toBe('$1,617.00');
    expect(money(3.5)).toBe('$3.50');
  });
  it('says how late an unpaid invoice is', () => {
    expect(docStatus({ kind: 'invoice', status: 'sent', overdue_days: 9 })).toEqual({ label: '9 days overdue', tone: 'warn' });
    expect(docStatus({ kind: 'invoice', status: 'paid', overdue_days: 9 }).tone).toBe('ok');
    expect(docStatus({ kind: 'quote', status: 'draft', overdue_days: 0 }).label).toBe('Draft');
  });
  it('puts the address and subject on top of a shared reminder', () => {
    expect(shareText({ to: 'jo@example.com', subject: 'Invoice INV-0001', body: 'Hi Jo' })).toBe('To: jo@example.com\nSubject: Invoice INV-0001\n\nHi Jo');
  });
});
