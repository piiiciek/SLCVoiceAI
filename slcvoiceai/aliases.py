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

import re

ALIASES: dict[str, tuple[str, ...]] = {
    # --- acknowledgement -------------------------------------------------
    # SLC does not always offer THANK YOU. When a ground crew exchange ends,
    # ROGER is often the only acknowledgement on screen - so thanking someone
    # for information has to reach it, or the command finds nothing at all.
    "roger": ("understood", "i understand", "copy", "copy that", "got it",
              "acknowledged", "affirm", "affirmative", "noted", "received",
              "ok", "okay", "alright", "sure", "thanks for the information",
              "thanks for the info", "thank you for the information",
              "good to know", "understood thanks", "noted thanks"),
    "will do": ("wilco", "we will", "i will do that", "consider it done"),
    "thank you": ("thanks", "thanks a lot", "cheers", "much appreciated",
                  "appreciate it", "ok thanks", "okay thanks", "thanks then",
                  "alright thanks", "thank you very much"),
    "thanks very much": ("thank you very much", "many thanks"),
    "no problem": ("thats fine", "no worries", "not a problem", "its fine"),

    # --- radio discipline ------------------------------------------------
    # Answering a call. Polish "prosze mowic" and "tak slucham" come back as
    # "Please speak" and "Yes I'm listening" - neither shares a word with
    # "GO AHEAD".
    "go ahead": ("send it", "send your message", "pass your message",
                 "im listening", "listening", "go on", "yes go ahead",
                 "please speak", "speak", "speak to me", "talk to me",
                 "yes im listening", "i am listening", "go ahead please",
                 "what is it", "what do you need", "you can speak",
                 # "prosze mowic" came back as "you can say" four times in
                 # one flight, with GO AHEAD on screen and refused each time.
                 "say", "you can say", "say it", "you may speak",
                 "we are listening", "i hear you go ahead"),
    "standby": ("stand by", "wait", "hold on", "one moment", "just a second",
                "give me a moment", "wait a moment"),
    "repeat transmission": ("say again", "repeat", "repeat that",
                            "i didnt catch that", "come again", "once more"),
    # Polish "nie wazne" comes back as "not important" or "Doesn't matter".
    # Both were declined in flight - offline as too weak, then by Gemini as
    # the pilot dismissing a thought rather than instructing anyone. Waving
    # the exchange away IS the instruction, and DISREGARD is its button.
    "disregard": ("never mind", "nevermind", "forget it", "ignore that",
                  "cancel that", "scratch that", "forget what i said",
                  "not important", "its not important", "doesnt matter",
                  "it doesnt matter", "does not matter", "no matter",
                  "forget about it", "leave it"),
    "radio check": ("how do you read", "do you read me", "check radio",
                    "mic check", "microphone check"),
    # "piec na piec" - the standard answer to a radio check - translates as
    # "5 by 5" or "5 to 5". It shares not one word with LOUD AND CLEAR, and
    # it does share one with 'Stand By', so in flight it pressed Stand By:
    # a wrong button rather than a decline, which is the worse failure.
    "loud and clear": ("i hear you", "reading you five", "hear you fine",
                       "you are clear", "five by five", "5 by 5", "5 to 5",
                       "five to five", "good to hear", "i hear you well",
                       "hear you well", "hear you loud and clear",
                       "reading you loud and clear", "signal is good"),

    # --- channels --------------------------------------------------------
    "ground crew": ("ground", "ground staff", "ground handling",
                    "ground service", "ground operation", "ramp",
                    "talk to ground", "call ground", "connect me with ground"),
    # "stewardesa" is the everyday Polish word and comes back as "stewardess",
    # which appears on no SLC button at all.
    "intercom": ("cabin crew", "call the cabin", "call the crew",
                 "flight attendant", "talk to the crew", "stewardess",
                 "stewardesses", "call the stewardess", "call the stewardesses",
                 "get me the crew", "i need the crew"),
    "purser to intercom": ("purser", "senior cabin crew", "get me the purser",
                           "chief flight attendant"),
    "cabin crew to intercom": ("all the crew to the intercom",
                               "everyone to the intercom"),
    "p a system": ("public address", "announcement", "tannoy", "speak to the cabin",
                   "address the passengers", "talk to the passengers",
                   "make an announcement", "pa"),
    "phone": ("company phone", "call the company", "telephone", "dial"),

    # --- boarding and doors ----------------------------------------------
    # Polish "ladowac" covers both letting passengers on and loading cargo, so
    # Whisper renders it literally as "load" or even "charge" - neither of
    # which resembles "BOARDING" at all. Observed in use: "ok, mozecie
    # ladowac jak jestescie gotowi" came back as "OK, you can load if you are
    # ready." and scored 0.49 against START BOARDING WHEN READY.
    "start boarding": ("begin boarding", "let them board", "board the passengers",
                       "passengers can board", "boarding can start",
                       "start letting them on", "load the passengers",
                       "charge the passengers", "you can load", "you can charge"),
    "start boarding when ready": ("you can load if you are ready",
                                  "you can load when ready",
                                  "load when you are ready",
                                  "charge passengers when you are ready",
                                  "board when you are ready",
                                  "let them on when you are ready"),
    "ready to start boarding": ("we are ready to board", "ready for boarding"),
    "request loading update": ("how is the loading going", "how does the loading look",
                               "loading status", "how is the charging",
                               "how does the charging look", "hows the loading",
                               "any update on loading", "how is loading"),
    "request offloading update": ("how is the offloading going",
                                  "offloading status", "how is the unloading"),
    "open the doors": ("open up", "doors open", "you can open the doors",
                       "let them out", "open a door"),
    "please close the doors": ("close up", "doors closed", "shut the doors",
                               "close the doors"),
    "connect jetway": ("attach the jetway", "bring the jetway", "jetway please",
                       "we need a jetway"),
    "connect stairs": ("attach the stairs", "bring the stairs", "stairs please",
                       "we need stairs"),
    "disconnect jetway": ("remove the jetway", "take the jetway away",
                          "detach the jetway", "jetway away",
                          "we are done with the jetway"),
    "disconnect stairs": ("remove the stairs", "take the stairs away",
                          "detach the stairs", "stairs away"),

    # --- GSX services ----------------------------------------------------
    "start catering": ("send the catering", "catering please", "we need catering",
                       "bring the catering", "let the catering come",
                       "catering to the aircraft"),
    "start refuelling": ("start fuelling", "we need fuel", "refuel the aircraft",
                         "send the fuel truck", "begin refuelling"),
    "start deboarding": ("start disembarking", "let them off", "begin deboarding"),
    "start deicing": ("de ice the aircraft", "we need deicing", "begin deicing"),

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
                  "seatbelt sign", "fasten the seatbelt", "fasten seat belts",
                  "seatbelt signs on", "turn on the seatbelt sign",
                  "seatbelt signs off", "turn off the seatbelt sign",
                  "belts on", "belts off"),
    "toggle doors": ("door management", "manage the doors", "show the doors"),
    "inflight services": ("services", "show the services", "cabin services",
                          "food and drinks"),
    "notifications": ("show notifications", "any messages", "alerts"),
    "stand by": ("pause", "put it on hold", "suspend"),

    # --- satisfaction ----------------------------------------------------
    # "super, moze byc" comes back as "It can be. Super." or "Great, it can
    # be done." - literal renderings of "moze byc" that share nothing with
    # "THAT'S PERFECT".
    "perfect": ("it can be", "it can be done", "great it can be done",
                "super it can be", "it can be super", "that can be",
                "that works", "yes that can be",
                "that will do", "good enough", "super", "great", "thats fine",
                "leave it there", "just right", "thats good"),
    "hows it going": ("how does it go", "how is it going", "how are things",
                      "how does it look", "how does it look like",
                      "how is it looking", "how does it fly",
                      "hows everything", "how is everything going",
                      "whats the situation"),
    "how are the passengers": ("how are the people", "are the passengers ok",
                               "how is the cabin"),

    # --- descent ----------------------------------------------------------
    # Even once Whisper says "descend" instead of "reduce", the wording it
    # picks varies from utterance to utterance - "shortly, we will descend",
    # "we are descending", "we started to lower" all came out of the same
    # Polish in one flight. SLC's two soon-ish buttons never appear together
    # on screen, so registering the same phrasings under both costs nothing.
    "descending soon": ("we will descend soon", "descending shortly",
                        "shortly we will descend", "we will be descending",
                        "soon we will descend", "we will start descending",
                        "starting our descent soon", "going down soon",
                        "descending in a moment", "we will lower soon"),
    "descending shortly": ("we will descend soon", "descending soon",
                           "shortly we will descend", "we will be descending",
                           "soon we will descend", "we will start descending",
                           "going down shortly", "descending in a moment"),
    "descent starting": ("we are descending", "starting our descent",
                         "beginning our descent", "starting the descent",
                         "we are starting to descend", "going down now",
                         "start lowering", "we are going down"),
    "started our descent": ("we started to lower", "we have started descending",
                            "we started going down", "we began our descent",
                            "we are on the way down"),

    # --- delays ----------------------------------------------------------
    # SLC has twenty delay-related buttons and several contain each other's
    # words, so the keys here are deliberately long: aliases_for takes only
    # the most specific match, and a loose key would hand a departure alias
    # to an arrival button.
    "short delay expected": ("short delay", "we can expect a short delay",
                             "small delay", "slight delay", "minor delay",
                             "brief delay", "a little delay"),
    "extended delay expected": ("long delay", "big delay", "significant delay",
                                "extended delay", "lengthy delay"),
    "medium delay expected": ("medium delay", "moderate delay"),
    "no departure delay": ("no delay", "no delays", "on time", "no delays expected",
                           "we are on schedule"),
    "were running behind": ("we are running behind", "a little behind",
                            "running a bit behind", "we are behind schedule"),
    "atc delay": ("air traffic control delay", "delay from atc",
                  "atc is holding us"),
    "short arrival delay expected": ("short arrival delay expected",
                                     "slight arrival delay"),
    "long arrival delay expected": ("long arrival delay expected",
                                    "big arrival delay"),
    "no arrival delay expected": ("arriving on time", "no arrival delay"),
    "slight enroute delay": ("small enroute delay", "slight delay enroute"),
    "extended enroute delay": ("long enroute delay", "big enroute delay"),
    "sorry for the delay": ("apologies for the delay", "sorry about the delay"),

    # --- plain answers ---------------------------------------------------
    "yes": ("yes please", "yep", "yeah", "go for it", "permission granted",
            "granted", "approved"),
    "no": ("no thanks", "nope", "negative", "not now", "denied"),
    "not at the moment": ("not right now", "maybe later", "later"),
    "back": ("go back", "previous", "return", "cancel", "back up"),
}


def aliases_for(button_name: str) -> tuple[str, ...]:
    """Every alias phrase registered for this button name.

    Matched on whole words, not raw substrings. "connect jetway" is literally
    a substring of "disconnect jetway", so substring matching handed every
    connect alias to the disconnect button and made the two indistinguishable
    - the exact confusion these opposites must never have.

    Longest key first, so a specific entry ("ready to start boarding")
    contributes before a generic one ("start boarding") when both apply.
    """
    words = _words(button_name)
    for key in sorted(ALIASES, key=len, reverse=True):
        if _contains_sequence(words, _words(key)):
            # Only the most specific key, never every key that happens to
            # match. "intercom" is a word inside "PURSER TO INTERCOM", so
            # applying both handed that button every generic intercom alias
            # and made it tie with INTERCOM > on "call the crew".
            return ALIASES[key]
    return ()


def _words(text: str) -> list[str]:
    """Lowercase words, with apostrophes closed up rather than split on.

    "HOW'S IT GOING?" must yield ["hows", "it", "going"] - splitting on the
    apostrophe gives ["how", "s", ...] and no key can ever match it.
    """
    return [w for w in re.split(r"[^a-z0-9]+", text.lower().replace("'", "")) if w]


def _contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    """Do `needle`'s words appear consecutively in `haystack`?"""
    if not needle or len(needle) > len(haystack):
        return False
    first = needle[0]
    for i in range(len(haystack) - len(needle) + 1):
        if haystack[i] == first and haystack[i:i + len(needle)] == needle:
            return True
    return False
