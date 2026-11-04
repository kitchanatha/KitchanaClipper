# Aion 2 streamers to ask for clip permission (English, Twitch)

Source: TwitchMetrics, English AION 2 category. "Avg" is average concurrent viewers while live, last 30 days.
"Growth" is followers gained in the last 30 days, which is the best public signal for a view spike.
Snapshot taken 2026-11-04.

- Popular: https://www.twitchmetrics.net/channels/popularity?game=AION+2&lang=en
- Growth: https://www.twitchmetrics.net/channels/growth?game=AION+2&lang=en

Nobody below has given permission yet. Add a streamer to `channels.aion2_clips.creators` in config.yaml
only after they say yes, and save their reply in this folder as proof.

## Ask first: Aion 2 regulars who are growing fast (most likely to say yes and clip well)
| # | Channel | Avg viewers | +Followers 30d | Why |
|---|---|---|---|---|
| 1 | https://www.twitch.tv/kanonxo | 6,918 | +20,211 | biggest dedicated Aion 2 streamer, live daily |
| 2 | https://www.twitch.tv/stoopzz | 5,659 | +13,955 | big growth; also has a second channel, stoopzzirl |
| 3 | https://www.twitch.tv/thewhelps | 2,198 | +12,139 | MMO veteran channel, strong spike |
| 4 | https://www.twitch.tv/jenny | 1,623 | +7,387 | fast growth |
| 5 | https://www.twitch.tv/fubgun | 3,132 | +5,563 | MMO streamer, live often |
| 6 | https://www.twitch.tv/datmodz | 2,397 | +5,374 | high hours watched |
| 7 | https://www.twitch.tv/wolves | 1,617 | +4,600 | |
| 8 | https://www.twitch.tv/mattshew | 942 | +4,596 | big spike for his size |
| 9 | https://www.twitch.tv/suntokki | 777 | +4,397 | big spike for her size |
| 10 | https://www.twitch.tv/bagginstv | 2,435 | +3,507 | |
| 11 | https://www.twitch.tv/kairicaitlyn | 495 | +3,113 | not a Partner yet, so likely to welcome exposure |

## Mid-size and rising (good response rate)
- **Sizable, still growing:** https://www.twitch.tv/j0eitv (644 avg viewers, +2,595), https://www.twitch.tv/sonii (1,040, +2,548), https://www.twitch.tv/johnpal (776, +2,512), https://www.twitch.tv/youmadsin (637, +2,405).
- **Similar size, newer growth:** https://www.twitch.tv/sweetily (1,250, +2,403), https://www.twitch.tv/elly (705, +2,291), https://www.twitch.tv/solidfps (944, +1,987), https://www.twitch.tv/dankrng (556, +1,948).
- **Smaller channels:** https://www.twitch.tv/alanstrikerz (+1,793), https://www.twitch.tv/saigax (+1,754), https://www.twitch.tv/notxeon (+1,544), https://www.twitch.tv/spidmmo (+1,135), https://www.twitch.tv/aionjanis (273 avg viewers).

## Long shots (huge channels: rarely answer, often have their own clip editors)
- https://www.twitch.tv/shroud: 8,643 avg viewers, but last seen in Aion 2 6 days ago.
- https://www.twitch.tv/fanfan: 2,443 avg viewers.
- https://www.twitch.tv/aceu: last seen in Aion 2 a month ago.

## Not a streamer
https://www.twitch.tv/aion2official is NCSOFT's own channel. Footage from it falls under NCSOFT's rules, not a streamer's permission.

---

## Message template (Twitch whisper, Discord, or the business email on their Twitch "About" page)

> Hi <name>! I run **<channel name>**, a new English YouTube channel for Aion 2 highlights.
> Could I use some of your Aion 2 Twitch clips in Shorts and weekly highlight videos?
> Every video credits you on screen ("CLIP: <NAME> - twitch.tv/<handle>") and links your channel
> at the top of the description. I only use clips you're happy with and will take anything down
> the same day if you ask. Is that OK with you, and are there any rules you'd like me to follow
> (no monetised videos, no certain clips, a specific link)?
> Thanks!

When someone says yes, add them to config.yaml. Fill in `date`, how they agreed (`how`), and `proof` (a screenshot or saved reply in this folder):
```yaml
      - name: Stoopzz
        platform: twitch
        channel: stoopzz
        url: https://www.twitch.tv/stoopzz
        permission: {date: "2026-11-05", how: "Twitch whisper", proof: "permissions/stoopzz.png", notes: ""}
```
