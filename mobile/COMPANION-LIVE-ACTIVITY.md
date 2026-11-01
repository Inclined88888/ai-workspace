# Companion Live Activity / Dynamic Island — Plan

## Status
**Not implemented.** No iPhone available for testing. This document describes a precise plan behind a feature flag `companionLiveActivity` (default `false`).

## Approach: `expo-widgets` (official, Expo SDK 58+)

`expo-widgets` is the supported way to build Live Activities in Expo. It:
- Runs the component inside the actual Live Activity process (not a JSON config)
- Supports Lock Screen banner, compact/minimal/expanded Dynamic Island presentations
- Works with push-to-start tokens for remote updates
- Requires a development build (not Expo Go)

### Required Setup

1. **Add dependency**
   ```bash
   npx expo install expo-widgets
   ```

2. **Configure app.json** — add a widget entry for the Live Activity:
   ```json
   {
     "expo": {
       "plugins": [
         ["expo-widgets", { "extensions": { "liveActivities": true } }]
       ],
       "ios": {
         "infoPlist": {
           "NSSupportsLiveActivities": true
         }
       }
     }
   }
   ```
   The plugin adds the `Supports Live Activities` capability and creates a Live Activity target.

3. **Create the Live Activity component** (e.g., `src/widgets/CompanionLiveActivity.tsx`):
   ```tsx
   'use client';
   import React from 'react';
   import { LiveActivity, createLiveActivity } from 'expo-widgets';
   import { Text, View } from 'react-native';

   type Props = {
     mood: 'idle' | 'working' | 'waiting' | 'done';
     agentCount: number;
     pendingCount: number;
     topAgent?: { name: string; status: string; lastStep?: string };
   };

   export const CompanionLiveActivity = ({ props }: LiveActivity<Props>) => (
     <View style={{ flex: 1, padding: 12 }}>
       {/* Lock Screen / Expanded Dynamic Island */}
       <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
         <Text style={{ fontSize: 20 }}>{moodEmoji(props.mood)}</Text>
         <View style={{ flex: 1 }}>
           <Text style={{ fontWeight: '600', fontSize: 16 }}>Companion</Text>
           <Text style={{ fontSize: 13, opacity: 0.7 }}>
             {props.agentCount} agent{props.agentCount !== 1 ? 's' : ''}{props.pendingCount ? ` · ${props.pendingCount} waiting` : ''}
           </Text>
         </View>
       </View>
       {props.topAgent && (
         <View style={{ marginTop: 8, padding: 8, borderWidth: 1, borderColor: '#333', borderRadius: 8 }}>
           <Text style={{ fontSize: 12, fontWeight: '600' }}>{props.topAgent.name}</Text>
           <Text style={{ fontSize: 11, opacity: 0.7 }}>{props.topAgent.status} · {props.topAgent.lastStep}</Text>
         </View>
       )}
     </View>
   );

   const moodEmoji = (m: string) => ({ idle: '😴', working: '🤖', waiting: '🤔', done: '✅' })[m] ?? '🤖';

   // Define presentations
   CompanionLiveActivity.lockScreen = ({ props }) => (
     <View style={{ flex: 1, padding: 16 }}>
       <Text style={{ fontWeight: 'bold', fontSize: 20 }}>Companion</Text>
       <Text style={{ fontSize: 14, marginTop: 4 }}>{props.agentCount} agents · {props.pendingCount} waiting</Text>
       {props.topAgent && <Text style={{ marginTop: 8, fontSize: 13 }}>{props.topAgent.name}: {props.topAgent.lastStep}</Text>}
     </View>
   );

   CompanionLiveActivity.dynamicIsland = ({ props }) => ({
     compactLeading: <Text>{moodEmoji(props.mood)}</Text>,
     compactTrailing: <Text>{props.pendingCount > 0 ? `${props.pendingCount}⏳` : ''}</Text>,
     minimal: <Text>{moodEmoji(props.mood)}</Text>,
     expanded: (
       <View style={{ flex: 1, padding: 8, gap: 4 }}>
         <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
           <Text style={{ fontWeight: '600' }}>Companion</Text>
           <Text>{moodEmoji(props.mood)} {props.agentCount}A</Text>
         </View>
         {props.topAgent && (
           <View style={{ borderTopWidth: 1, borderColor: '#333', paddingTop: 4 }}>
             <Text style={{ fontSize: 12 }}>{props.topAgent.name}</Text>
             <Text style={{ fontSize: 11, opacity: 0.7 }}>{props.topAgent.lastStep}</Text>
           </View>
         )}
       </View>
     ),
   });

   export const companionLiveActivity = createLiveActivity('CompanionLiveActivity', CompanionLiveActivity);
   ```

4. **Start/update from the Companion screen** (guarded by feature flag):
   ```ts
   import { companionLiveActivity } from '@/widgets/CompanionLiveActivity';

   // In Companion.tsx, when state changes:
   if (prefs.companionLiveActivity) {
     if (!liveActivityInstance) {
       liveActivityInstance = await companionLiveActivity.start({
         mood: s.mood,
         agentCount: agents.length,
         pendingCount: pendingRequests.length,
         topAgent: agents[0] ? { name: agents[0].name, status: agents[0].status, lastStep: agents[0].steps.slice(-1)[0]?.text } : undefined,
       });
     } else {
       await liveActivityInstance.update({
         mood: s.mood,
         agentCount: agents.length,
         pendingCount: pendingRequests.length,
         topAgent: agents[0] ? { name: agents[0].name, status: agents[0].status, lastStep: agents[0].steps.slice(-1)[0]?.text } : undefined,
       });
     }
   }
   ```

5. **End when app backgrounds or no agents**:
   ```ts
   useEffect(() => {
     return () => { if (liveActivityInstance) liveActivityInstance.end('default'); };
   }, []);
   ```

### Push-to-Start (Remote Updates)

For updates when the app is not running:
- Backend sends a push with `type: "companion"` and includes a `live_activity` payload
- The Live Activity target receives the push token via `LiveActivity.addPushTokenListener`
- Requires App Groups capability shared between main app and Live Activity target
- Expo widgets handles the push token registration automatically when `enablePushNotifications: true` in plugin config

### Feature Flag

Add to `Prefs` in `src/lib/storage.ts`:
```ts
companionLiveActivity: false,
```

Add toggle in Settings screen under a "Companion" section.

## Migration Path if Needed

If `expo-widgets` proves unstable, the fallback is a config plugin + Swift Widget Extension:
- Create `ios/CompanionLiveActivity/CompanionLiveActivity.swift` with `WidgetBundle` + `LiveActivity`
- Use `expo-build-properties` to add the target
- Communicate via App Groups `UserDefaults` or `DarwinNotificationCenter`
- More maintenance burden; not recommended unless `expo-widgets` blocks us.

## Verification Checklist (when iPhone available)

- [ ] Development build installs and runs
- [ ] Live Activity appears on Lock Screen when Companion screen opens with active agents
- [ ] Dynamic Island shows compact/minimal/expanded states correctly
- [ ] Updates reflect within 1-2s of state change
- [ ] Live Activity ends when no agents are running and no requests pending
- [ ] Push-to-start wakes Live Activity from terminated state (optional, phase 2)
- [ ] No memory leaks or crashes in 30-min session

## Rollback

Set `companionLiveActivity: false` in prefs; remove the widget entry from app.json; delete the widget file.