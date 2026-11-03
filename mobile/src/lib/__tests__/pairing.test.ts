import { formatCode, isCompleteCode, normaliseAddress, normaliseCode, parsePairingUrl } from '../pairing';

describe('pairing codes', () => {
  it('normalises and formats', () => {
    expect(normaliseCode(' abcd-efgh ')).toBe('ABCDEFGH');
    expect(formatCode('abcdefgh')).toBe('ABCD-EFGH');
    expect(formatCode('abc')).toBe('ABC');
    expect(normaliseCode('ABCD-EFGH-JKLM')).toBe('ABCDEFGH');
  });
  it('accepts only the desktop alphabet (no 0/O/1/I/L)', () => {
    expect(isCompleteCode('ABCD-EFGH')).toBe(true);
    expect(isCompleteCode('ABCD-EFG0')).toBe(false);
    expect(isCompleteCode('ABCD-EFGI')).toBe(false);
    expect(isCompleteCode('ABCD')).toBe(false);
  });
});

describe('addresses', () => {
  it('adds http and the phone port to a bare host', () => {
    expect(normaliseAddress('192.168.1.20')).toBe('http://192.168.1.20:8766');
    expect(normaliseAddress('192.168.1.20:9000')).toBe('http://192.168.1.20:9000');
  });
  it('keeps what a full URL says', () => {
    expect(normaliseAddress('https://desk.tailnet.ts.net/')).toBe('https://desk.tailnet.ts.net');
    expect(normaliseAddress('http://10.0.0.2:8765')).toBe('http://10.0.0.2:8765');
  });
  it('rejects junk', () => {
    expect(normaliseAddress('')).toBeNull();
    expect(normaliseAddress('http://')).toBeNull();
  });
});

describe('QR payload', () => {
  it('parses the desktop QR', () => {
    const qr = 'aiworkspace://pair?c=ABCD-EFGH&u=http%3A%2F%2F192.168.1.20%3A8766&u=https%3A%2F%2Fdesk.example.ts.net&n=Studio+Mac';
    expect(parsePairingUrl(qr)).toEqual({ code: 'ABCDEFGH', urls: ['http://192.168.1.20:8766', 'https://desk.example.ts.net'], name: 'Studio Mac' });
  });
  it('dedupes addresses and defaults the name', () => {
    const r = parsePairingUrl('aiworkspace://pair?c=ABCDEFGH&u=192.168.1.20&u=http%3A%2F%2F192.168.1.20%3A8766');
    expect(r?.urls).toEqual(['http://192.168.1.20:8766']);
    expect(r?.name).toBe('AI Workspace');
  });
  it('refuses other QR codes', () => {
    expect(parsePairingUrl('https://example.com')).toBeNull();
    expect(parsePairingUrl('aiworkspace://pair?c=SHORT&u=1.2.3.4')).toBeNull();
    expect(parsePairingUrl('aiworkspace://pair?c=ABCDEFGH')).toBeNull();
    expect(parsePairingUrl('aiworkspace://pair?c=ABCDEFGH&u=%E0%A4%A')).toBeNull();
  });
});
