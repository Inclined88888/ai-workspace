/**
 * Voice input: on-device speech to text with expo-speech-recognition (iOS SFSpeechRecognizer, Android
 * SpeechRecognizer). It is a native module, so it works in a development or store build, not in Expo Go; there the
 * mic button explains that and the keyboard's own dictation key still works.
 */
type Sub = { remove: () => void };
type Mod = {
  requestPermissionsAsync: () => Promise<{ granted: boolean }>;
  isRecognitionAvailable: () => boolean;
  supportsOnDeviceRecognition: () => boolean;
  start: (o: Record<string, unknown>) => void;
  stop: () => void;
  abort: () => void;
  addListener: (name: string, fn: (e: any) => void) => Sub;
};

let mod: Mod | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  mod = require('expo-speech-recognition').ExpoSpeechRecognitionModule as Mod;
} catch {
  mod = null; // Expo Go: the native module is not in the binary
}

export function voiceAvailable(): boolean {
  try {
    return !!mod && mod.isRecognitionAvailable();
  } catch {
    return false;
  }
}

export type VoiceSession = { stop: () => void; cancel: () => void };

export async function startDictation(
  lang: string,
  onText: (text: string, final: boolean) => void,
  onEnd: (error?: string) => void,
  onVolume?: (value: number) => void,
): Promise<VoiceSession | null> {
  if (!mod) {
    onEnd('Voice input needs the installed app (a development or TestFlight build). In Expo Go, use the keyboard mic.');
    return null;
  }
  const perm = await mod.requestPermissionsAsync();
  if (!perm.granted) {
    onEnd('Allow microphone and speech recognition in Settings to dictate.');
    return null;
  }
  const subs: Sub[] = [];
  const cleanup = () => subs.splice(0).forEach((s) => s.remove());
  subs.push(
    mod.addListener('result', (e) => onText(e?.results?.[0]?.transcript ?? '', !!e?.isFinal)),
    mod.addListener('error', (e) => {
      cleanup();
      onEnd(e?.message || 'Dictation stopped');
    }),
    ...(onVolume ? [mod.addListener('volumechange', (e) => onVolume(Number(e?.value)))] : []),
    mod.addListener('end', () => {
      cleanup();
      onEnd();
    }),
  );
  let onDevice = false;
  try {
    onDevice = mod.supportsOnDeviceRecognition();
  } catch {
    onDevice = false;
  }
  mod.start({ lang, interimResults: true, continuous: false, requiresOnDeviceRecognition: onDevice, addsPunctuation: true, ...(onVolume ? { volumeChangeEventOptions: { enabled: true, intervalMillis: 80 } } : {}) });
  return {
    stop: () => mod?.stop(),
    cancel: () => {
      cleanup();
      mod?.abort();
    },
  };
}
