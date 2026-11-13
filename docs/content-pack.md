# BRIDGEWORDS content pack

Everything below is a draft. All people, names and calls are fictional (the challenge requires synthetic data). Have a native Kannada speaker check every Kannada line before recording.

Companion file: `symbols.csv`, with 67 tiles in 7 categories (English, Hindi, Kannada, emoji). It opens in Excel or Google Sheets for editing.

---

## 1. Demo persona (fictional)

**Aarav, 19, Bengaluru.** He has cerebral palsy and very little clear speech, but he understands everything, and he uses a tablet propped on his wheelchair tray. His family speaks Hinglish at home, and the local shops speak Kannada and English. He can tap large buttons reliably, and typing letters is slow.

What he wants: to answer people himself, without someone guessing for him.

---

## 2. Scenario scripts

### Scenario A: "Lunch at home" (Hinglish, listening mode)

| # | Who | Says / does | What the app should do |
|---|---|---|---|
| 1 | Mummy (from the kitchen, pressure cooker in the background) | "Aarav, khana khaoge? Dal chawal hai, ya khichdi bana doon?" | Prisma transcribes it. The top reply row changes to **Dal chawal · Khichdi · Not hungry · Later · Something else** |
| 2 | Aarav | Taps **Khichdi**, then **Soft food** | Suggests: (a) "Khichdi, please. Thodi soft banana." (b) "Khichdi chahiye, ekdum naram." |
| 3 | Aarav | Picks (a) | Timbre speaks it in Aarav's chosen voice |
| 4 | Mummy | "Theek hai. Saath mein dahi bhi doon?" | Reply row: **Yes · No · Later · Something else** |
| 5 | Aarav | Taps **No**, then **Water** | Suggests "Nahi, dahi nahi. Bas paani de do." |
| 6 | Mummy | "Aur kal doctor ke yahan chalna hai, yaad hai na?" | **Ambiguity moment.** If Aarav taps **Pain + Tomorrow**, the app does NOT guess. It asks with a yes/no: "Do you mean you have pain now, or about tomorrow's doctor visit?" |

What it shows: replies built from what was just said (listening), code-mixed speech, the user always choosing, and the app asking instead of guessing.

### Scenario B: "Calling the chemist" (Kannada + English, phone mode on speaker)

Setup: a normal phone call on speaker, with the tablet next to it.

| # | Who | Says / does | What the app should do |
|---|---|---|---|
| 1 | Chemist (traffic noise behind) | "Hello, Sri Sai Medicals. Heli sir, enu beku?" | Reply row: **Medicine · Something else · Please say again** |
| 2 | Aarav | Taps **Medicine** and types "para" | Suggests: "Namaskara. Nanage paracetamol beku, ondu strip." (with a Hindi or English option if preferred) |
| 3 | Chemist | "500 mg aa, 650 aa?" | Reply row: **500 · 650 · Don't know · Something else** |
| 4 | Aarav | Taps **500** | Speaks "500 mg, ondu strip." |
| 5 | Chemist | "Sari. Home delivery beka? Address heli." | Reply row: **Yes, deliver · I will collect · Something else**. Address comes from a saved profile and is only spoken after Aarav confirms it |
| 6 | Aarav | Taps **Yes, deliver**, then confirms the saved address | Speaks the address and "Dhanyavada" |

What it shows: a phone call he could never make alone, noisy telephone audio, Kannada, and a saved profile that never speaks without confirmation.

---

## 3. Tricky test lines

Say these out loud near the tablet. Note what Prisma heard, whether the suggestions made sense, and how long it took.

1. "Khana khaoge ki baad mein?" (with a pressure cooker whistle)
2. "Kal chalna hai." (कल is both yesterday and tomorrow. The app should confirm)
3. "Dawai li ki nahi? Woh white wali, subah wali." (vague reference)
4. "Aarav, phone pe kaun hai? Rahul? Ya Riya?" (similar names)
5. "Thoda… ek minute… haan, toh main keh rahi thi ki…" (half-sentences and pauses: tests end-of-speech detection)
6. "Nimge water beka, illa juice?" (Kannada + English)
7. "Anna, delivery sanje aagutte, OK na?" (Kannada, fast)
8. "Don't you want to go out today?" (negative question: "Yes" is ambiguous)
9. "Kya tumhe dard nahi ho raha?" (same negation trap in Hindi)
10. Two people talking at once: "Chai?" / "Nahi, pehle khana."
11. Fan on full, said from 3 metres away: "TV band kar doon?"
12. Over speakerphone with traffic: "Order number ek-do-teen-chaar confirm karo."
13. "Haan haan theek hai, theek hai." (filler: should not trigger new suggestions)
14. The tablet's own Timbre voice playing back (phone mode): the app must NOT treat this as the partner speaking
15. Silence for 20 seconds, then "Aarav?" (should wake, not time out)

For each one, record: what Prisma heard, whether the top 3 suggestions were correct, the time to the first suggestion, and pass/fail.

---

## 4. 60-second demo video

**Rule:** no one acts disabled. A teammate operates the tablet, and an on-screen label reads "Prototype · synthetic scenario · tablet operated by team member".

| Time | Visual | Audio / caption |
|---|---|---|
| 0–4 s | Black screen, then a close-up of the tablet. Caption: **"What if you could hear everything, but couldn't answer?"** | Silence, then a pressure cooker whistle |
| 4–10 s | Mummy (off-screen) asks "Khana khaoge? Dal chawal ya khichdi?" The reply row visibly reshuffles | Caption: "BRIDGEWORDS listens to the conversation (Gnani Prisma)" |
| 10–18 s | One tap on Khichdi, one on Soft food, then the suggested sentence appears. Tap to approve | Caption: "2 taps instead of 8" |
| 18–22 s | Timbre speaks "Khichdi, please. Thodi soft banana." | Caption: "His chosen voice (Gnani Timbre)" |
| 22–32 s | The ambiguity moment: Pain + Tomorrow. The app asks "Pain now, or about tomorrow's doctor visit?" User taps "Now" | Caption: **"It never puts words in his mouth."** |
| 32–48 s | Hard cut to a phone call on speaker with a Kannada chemist and traffic noise. Suggestions appear and the tablet speaks the order | Caption: "First phone call he can make on his own · noisy line · Kannada" |
| 48–55 s | Quick montage: Hinglish, Kannada, English tiles. Simple diagram: Prisma, then LLM, then Timbre | Caption: "Built on Gnani Prisma + Timbre" |
| 55–60 s | Logo, tagline | **"BRIDGEWORDS: their words, their choice."** Then #GnaniAI #GreatIndianAIInternshipChallenge |

Shoot it vertical (9:16) for LinkedIn and X, with English subtitles burned in. Record the screen with the phone or OBS, and use a second phone for the cutaway shots.

---

## 5. LinkedIn / X post draft

> Millions of people in India understand every word said to them, but can't answer out loud.
>
> Communication boards make them dig through menus for every reply. By the time they've built a sentence, the conversation has moved on.
>
> We built **BRIDGEWORDS** for the Great Indian AI Internship Challenge by Gnani AI.
>
> 🎧 It *listens* to the person talking (Gnani Prisma, even Hinglish and noisy phone calls)
> 💡 It suggests replies that fit what was just said, so 2 taps instead of 8
> ❓ When the meaning is unclear, it asks instead of guessing
> 🗣️ It speaks the approved reply in a voice the user chose (Gnani Timbre)
> 📞 It even works on speakerphone calls, so users can call a chemist on their own
>
> Prototype with synthetic scenarios. We'd love feedback from special educators, speech therapists and AAC users.
>
> Built by [Name 1] & [Name 2]
> #GnaniAI #GreatIndianAIInternshipChallenge #Accessibility #AAC

For X, shorten it to the first line, the 🎧/💡/🗣️ points, the video and both hashtags.

---

## 6. Submission form draft

**Project title:** BRIDGEWORDS: listening communication aid for people with speech disabilities

**Problem:** People with cerebral palsy, motor neurone disease, aphasia or other speech disabilities often understand everything but can't reply in time. Symbol boards (AAC) are slow: building one sentence can take many taps, so conversations move on without them and phone calls are almost impossible. Most AAC apps are English-first and don't understand the Hinglish or regional languages spoken around the user.

**Solution:** BRIDGEWORDS listens to the conversation partner with Gnani Prisma (including code-mixed and noisy telephone speech) and instantly turns the board into replies that fit what was just said. The user taps a few tiles. An LLM proposes full sentences, and when the meaning is unclear it asks a yes/no question instead of guessing. Nothing is spoken until the user approves it. The approved reply is spoken with Gnani Timbre in the user's language and chosen voice. Phone mode lets the user handle a speakerphone call on their own.

**Target users:** People with limited functional speech who can tap or select (and their families); special schools, therapy centres and rehabilitation hospitals; later, NGOs and government disability programmes.

**Gnani models / APIs used:** Prisma (speech-to-text: listening to the partner, code-mixed and telephonic audio); Timbre v2.5 (text-to-speech: speaking the user's approved replies); [Voice cloning: only if it worked on our credits].

**Each member's contribution:** *(fill in honestly; you're interviewed separately)*
- [Sachin]: …
- [Teammate]: …
- AI tools used: Claude Code for most of the application code; Claude for the content drafts (symbol list, scripts, copy). Edit to match what actually happened.

**Safety and limits:** synthetic data only; the user approves every message; urgent tiles (Help, Hard to breathe) speak instantly with no AI step; it's a prototype not yet tested with AAC users.
