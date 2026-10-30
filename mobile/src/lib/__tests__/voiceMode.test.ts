// feat/v02-anywhere-voice: voice mode on the phone (spoken approvals and what gets read out).
import { approvalPrompt, canSpeak, say, speakable, spokenDecision } from '../voiceMode';

const mockSpoken: string[] = [];
jest.mock('expo-speech', () => ({
  speak: (t: string, o: { onDone?: () => void }) => {
    mockSpoken.push(t);
    o.onDone?.();
  },
  stop: () => undefined,
}));

describe('spoken approvals', () => {
  it('approves only a clear yes', () => {
    for (const t of ['yes', 'Yes, send it.', 'go ahead', 'approve', 'yep do it']) expect(spokenDecision(t)).toBe('approve');
  });
  it('denies any no, even after a yes', () => {
    for (const t of ['no', "don't", 'yes... no wait', 'hold on', 'not yet', 'cancel']) expect(spokenDecision(t)).toBe('deny');
  });
  it('asks again when it is unclear', () => {
    for (const t of ['', 'okay', 'sure', 'maybe', 'yes but to Sam', 'yes?', 'send it to the team instead']) expect(spokenDecision(t)).toBe('unclear');
  });
  it('reads the request out in plain words', () => {
    expect(approvalPrompt('send_email')).toMatch(/^I need your OK to send email\. It's on screen\./);
  });
});

describe('reading answers out', () => {
  it('drops markdown, code and links, and shortens long answers', () => {
    expect(speakable('**Done.** See [the note](x) at https://a.b/c')).toBe('Done. See the note at the link on screen');
    expect(speakable('```\ncode\n```')).toBe('(code on screen)');
    const long = 'This is one sentence. '.repeat(200);
    expect(speakable(long).length).toBeLessThan(1300);
    expect(speakable(long)).toMatch(/The rest is on screen\.$/);
  });
  it('speaks with expo-speech when it is in the build', async () => {
    expect(canSpeak()).toBe(true);
    await say('*Hello* there');
    expect(mockSpoken).toEqual(['Hello there']);
  });
});
