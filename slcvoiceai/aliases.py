"""Phrases that mean the same as an SLC button but share no letters with it.

String similarity compares spelling, not meaning, so "understood" scores
almost zero against "ROGER" even though a pilot saying one means the other.
Whisper's translation of Polish widens the gap: "zrozumialem" comes back as
"I understand", further still from the button's wording.

Aviation phraseology is a closed vocabulary, so a curated table closes most
of that gap without a model, a network call, or a byte of VRAM.

Keys are matched as substrings of the button's own name, case-insensitively,
so one entry covers every variant SLC shows ("ROGER", "Roger That", ...).
Values are scored alongside the button name; the best score wins.
"""

from __future__ import annotations

ALIASES: dict[str, tuple[str, ...]] = {
    # --- acknowledgement -------------------------------------------------
    "roger": ("understood", "i understand", "copy", "copy that", "got it",
              "acknowledged", "affirm", "affirmative", "noted", "received",
              "okay", "alright", "sure"),
    "will do": ("wilco", "we will", "i will do that", "consider it done"),
    "thank you": ("thanks", "thanks a lot", "cheers", "much appreciated",
                  "appreciate it"),
    "thanks very much": ("thank you very much", "many thanks"),
    "no problem": ("thats fine", "no worries", "not a problem", "its fine"),

    # --- radio discipline ------------------------------------------------
    "go ahead": ("send it", "send your message", "pass your message",
                 "im listening", "listening", "go on", "yes go ahead"),
    "standby": ("stand by", "wait", "hold on", "one moment", "just a second",
                "give me a moment", "wait a moment"),
    "repeat transmission": ("say again", "repeat", "repeat that",
                            "i didnt catch that", "come again", "once more"),
    "disregard": ("never mind", "forget it", "ignore that", "cancel that",
                  "scratch that", "forget what i said"),
    "radio check": ("how do you read", "do you read me", "check radio",
                    "mic check", "microphone check"),
    "loud and clear": ("i hear you", "reading you five", "hear you fine",
                       "you are clear"),

    # --- channels --------------------------------------------------------
    "ground crew": ("ground", "ground staff", "ground handling",
                    "ground service", "ground operation", "ramp",
                    "talk to ground", "call ground", "connect me with ground"),
    "intercom": ("cabin crew", "call the cabin", "call the crew", "purser",
                 "flight attendant", "senior cabin crew", "talk to the crew"),
    "p a system": ("public address", "announcement", "tannoy", "speak to the cabin",
                   "address the passengers", "talk to the passengers",
                   "make an announcement", "pa"),
    "phone": ("company phone", "call the company", "telephone", "dial"),

    # --- boarding and doors ----------------------------------------------
    "start boarding": ("begin boarding", "let them board", "board the passengers",
                       "passengers can board", "boarding can start",
                       "start letting them on"),
    "ready to start boarding": ("we are ready to board", "ready for boarding"),
    "open the doors": ("open up", "doors open", "you can open the doors",
                       "let them out", "open a door"),
    "please close the doors": ("close up", "doors closed", "shut the doors",
                               "close the doors"),
    "connect jetway": ("attach the jetway", "bring the jetway", "jetway please",
                       "we need a jetway"),
    "connect stairs": ("attach the stairs", "bring the stairs", "stairs please",
                       "we need stairs"),

    # --- departure -------------------------------------------------------
    "ready for pushback": ("ready to push", "we can push", "request pushback",
                           "push when ready", "we are good to push",
                           "lets push back", "we can start pushing"),
    "start pushback": ("begin pushback", "start pushing", "push now"),
    "stop pushback": ("stop pushing", "hold pushback", "stop the push"),
    "parking brake released": ("brakes released", "brake is off", "brakes off"),
    "parking brake is set": ("brakes set", "brake is on", "brakes on"),
    "ready to go now": ("we are ready", "ready now", "lets go", "good to go"),

    # --- engines and APU -------------------------------------------------
    "starting the apu": ("start the apu", "we are ready to start the apu",
                         "apu start", "ready for apu", "fire up the apu",
                         "starting apu"),
    "hotel startup": ("hotel mode", "start hotel mode"),
    "please disconnect gpu": ("disconnect the gpu", "remove ground power",
                              "gpu off", "we dont need ground power"),
    "single engine taxi": ("one engine taxi", "taxi on one engine"),

    # --- cabin -----------------------------------------------------------
    "release the cabin crew": ("crew can move", "crew are free",
                               "you can move around", "free to move about",
                               "crew released"),
    "seats for takeoff": ("take your seats", "be seated for takeoff",
                          "sit down for takeoff", "prepare for takeoff"),
    "take seats for landing": ("be seated for landing", "sit down for landing",
                               "seats for landing"),
    "prepare cabin for landing": ("secure the cabin", "cabin secure",
                                  "get the cabin ready"),
    "welcome aboard": ("welcome the passengers", "greet the passengers",
                       "welcome announcement"),

    # --- windows and toggles ---------------------------------------------
    "available phrases window": ("what can i say", "show me the commands",
                                 "show the phrases", "help", "show help",
                                 "available commands", "what are my options"),
    "narration window": ("show the transcript", "what was said",
                         "show narration", "show the dialogue"),
    "check list": ("checklist", "show the checklist", "open the checklist",
                   "show my score", "scoring"),
    "aircraft layout": ("show the cabin", "cabin layout", "show the seats",
                        "seat map", "show the aircraft"),
    "seatbelts": ("seat belt", "seat belts", "belt sign", "fasten seatbelts",
                  "seatbelt sign"),
    "toggle doors": ("door management", "manage the doors", "show the doors"),
    "inflight services": ("services", "show the services", "cabin services",
                          "food and drinks"),
    "notifications": ("show notifications", "any messages", "alerts"),
    "stand by": ("pause", "put it on hold", "suspend"),

    # --- plain answers ---------------------------------------------------
    "yes": ("yes please", "yep", "yeah", "go for it", "permission granted",
            "granted", "approved"),
    "no": ("no thanks", "nope", "negative", "not now", "denied"),
    "not at the moment": ("not right now", "maybe later", "later"),
    "back": ("go back", "previous", "return", "cancel", "back up"),
}


def aliases_for(button_name: str) -> tuple[str, ...]:
    """Every alias phrase registered for this button name.

    Longest key first so that a specific entry ("ready to start boarding")
    contributes before a generic one ("start boarding") when both apply.
    """
    low = button_name.strip().lower()
    out: list[str] = []
    for key in sorted(ALIASES, key=len, reverse=True):
        if key in low:
            out.extend(ALIASES[key])
    return tuple(out)
