/**
 * Photo and file attachments. Photos come from the camera or library (re-encoded to JPEG, so HEIC never surprises
 * the computer); files come from the Files app, limited to the image and audio types the computer accepts.
 * Everything is uploaded as base64 to POST /api/mobile/attachments (10 MB cap) and referenced by id in the turn.
 */
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import { File } from 'expo-file-system';

export const MAX_BYTES = 10 * 1024 * 1024;
export const ACCEPTED = ['image/jpeg', 'image/png', 'image/heic', 'image/webp', 'audio/m4a', 'audio/mp4', 'audio/x-m4a', 'audio/aac', 'audio/mpeg', 'audio/wav'];

export type Pending = { key: string; kind: 'photo' | 'voice'; mime: string; name: string; uri: string; data: string; bytes: number };

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

export function bytesToBase64(bytes: Uint8Array): string {
  let out = '';
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + B64[n & 63];
  }
  const rest = bytes.length - i;
  if (rest === 1) {
    const n = bytes[i] << 16;
    out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + '==';
  } else if (rest === 2) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
    out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63] + B64[(n >> 6) & 63] + '=';
  }
  return out;
}

export const base64Bytes = (b64: string) => Math.floor((b64.length * 3) / 4) - (b64.endsWith('==') ? 2 : b64.endsWith('=') ? 1 : 0);

export function kindFor(mime: string): 'photo' | 'voice' | null {
  if (!ACCEPTED.includes(mime)) return null;
  return mime.startsWith('audio/') ? 'voice' : 'photo';
}

const key = () => Math.random().toString(36).slice(2, 10);

export async function pickPhoto(fromCamera: boolean): Promise<Pending | string | null> {
  const perm = fromCamera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) return fromCamera ? 'Allow the camera in Settings to take a photo.' : 'Allow photo access in Settings to attach one.';
  const opts: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.6, base64: true, exif: false };
  const r = fromCamera ? await ImagePicker.launchCameraAsync(opts) : await ImagePicker.launchImageLibraryAsync(opts);
  if (r.canceled || !r.assets?.[0]?.base64) return null;
  const a = r.assets[0];
  const data = a.base64 as string;
  const bytes = base64Bytes(data);
  if (bytes > MAX_BYTES) return 'That photo is over 10 MB.';
  return { key: key(), kind: 'photo', mime: 'image/jpeg', name: a.fileName || 'photo.jpg', uri: a.uri, data, bytes };
}

export async function pickFile(): Promise<Pending | string | null> {
  const r = await DocumentPicker.getDocumentAsync({ type: ['image/*', 'audio/*'], copyToCacheDirectory: true, multiple: false });
  if (r.canceled || !r.assets?.[0]) return null;
  const a = r.assets[0];
  const mime = (a.mimeType || '').toLowerCase();
  const kind = kindFor(mime);
  if (!kind) return 'Your computer accepts photos (JPEG, PNG, HEIC, WebP) and audio (M4A, AAC, MP3, WAV).';
  if ((a.size ?? 0) > MAX_BYTES) return 'That file is over 10 MB.';
  const buf = a.file ? await a.file.arrayBuffer() : await new File(a.uri).arrayBuffer();
  const data = bytesToBase64(new Uint8Array(buf));
  return { key: key(), kind, mime, name: a.name || 'file', uri: a.uri, data, bytes: buf.byteLength };
}
