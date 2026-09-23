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
    # Answering "how are you feeling?" from the cabin. Polish
    # "wszystko w porzadku" came back as "all good" - not one word
    # of which is in the button - and the pilot then said it three
    # more times before giving up and switching to English.
    "feeling fine": ("all good", "all is well", "everything is fine",
                     "everything is ok", "everything is good", "im ok",
                     "im fine", "im good", "doing fine", "doing well",
                     "no complaints", "all fine"),

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
                 "we are listening", "i hear you go ahead",
                 # Whisper renders Polish "mozesz mowic" as any of these
                 # from one flight to the next; "you can speak" was already
                 # here and "you can continue" was not, so the same words in
                 # Polish worked or failed on the toss of a coin.
                 "you can continue", "you can go ahead", "please continue",
                 # How base renders Polish "slucham" and "mozesz mowic"
                 # when the simulator has the card. Bare "listen" is one
                 # word, so the whole-utterance guard keeps it from
                 # firing on "listen to the passengers".
                 "listen", "can you tell", "can you tell me",
                 "you can tell me", "tell me", "im all ears"),
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
    # Polish "mozecie tankowac" is permission, not an order, and comes
    # back as "you can refuel" - which scored 0.46 against the button
    # and fell under the floor. The same shape covers the rest of the
    # ground services a captain gives permission for.
    "start refuelling": ("start fuelling", "we need fuel", "refuel the aircraft",
                         "send the fuel truck", "begin refuelling",
                         "you can refuel", "you can start refuelling",
                         "you can fuel", "you may refuel", "you can start fuelling",
                         "fuel us up", "we are ready for fuel",
                         "go ahead with refuelling", "you can begin refuelling"),
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
    # Polish "zajac miejsca" is literally "to take places", and that is
    # how Whisper renders it - "we ask for a place", "take a look at
    # the place". The button says SEATS, so the two share no word at
    # all and the announcement was refused twice in a row.
    "seats for takeoff": ("take your seats", "be seated for takeoff",
                          "sit down for takeoff", "prepare for takeoff",
                          "take your place", "take your places",
                          "take a place", "please take your places",
                          "we ask for a place", "we ask you to take your places",
                          "find your places", "return to your places"),
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

    # --- apologising ------------------------------------------------------
    # Polish "przepraszam" comes back as "I'm sorry", which shares nothing
    # with APOLOGIES - measured at 0.12 against it, 272nd of 295 lines.
    "apologies": ("im sorry", "i am sorry", "i apologise", "we apologise",
                  "my apologies", "our apologies", "i do apologise"),
    "thats my bad": ("my fault", "my mistake", "that was my fault", "my bad",
                     "sorry that was me"),

    # --- seating the cabin -------------------------------------------------
    # Telling the cabin to sit down was refused outright with the button on
    # screen: "sit down please" 0.38, "everyone sit down" 0.32, "stay in
    # your seats" 0.54 - all under the floor. Polish "usiadzcie" comes back
    # as "sit down", and SLC's word is "seated".
    #
    # The keys are the full button names on purpose, and not "be seated":
    # that also matches BE SEATED FOR TAKEOFF NOW and BE SEATED FOR LANDING
    # NOW, and since only the longest matching key applies, a short key here
    # would hand those two every alias below and make the phase-less phrase
    # ambiguous against them.
    "please be seated": ("sit down", "sit down please", "please sit down",
                         "you can sit down", "you may sit down",
                         "everyone sit down"),
    "please remain seated": ("stay seated", "stay in your seats",
                             "remain in your seats", "please stay seated",
                             "keep your seats", "do not get up"),
    # The same instruction with a phase attached, which is what a pilot
    # actually says: "mozecie usiasc, zaraz bedziemy ladowac" comes back as
    # "you can sit down, we will land in a moment". Recorded in a real
    # flight, where it reached BE SEATED FOR LANDING NOW at 0.35 and
    # nothing cleared the floor.
    "be seated for landing": ("sit down we are landing",
                              "you can sit down we are landing",
                              "sit down for landing",
                              "everyone sit down we are landing",
                              "sit down we will land in a moment"),
    # Polish "zaraz startujemy, mozecie usiasc" is a warning plus
    # permission, and base drops the seating half of it entirely -
    # "can you take off, start now?" left the right button top of the
    # list at 0.57, under the floor, and the cloud then read the
    # wreckage literally and refused. The takeoff half is enough to
    # go on: SLC has no button for performing a takeoff, so a captain
    # saying one to the cabin means the seats.
    "be seated for takeoff": ("sit down we are taking off",
                              "sit down for takeoff",
                              "everyone sit down we are taking off",
                              "we are taking off", "we are taking off now",
                              "we are taking off shortly", "taking off now",
                              "we are departing shortly", "you can sit down",
                              "you can take your seats", "please sit down",
                              "take your seats now"),

    # --- turning back ------------------------------------------------------
    # Covers RETURN TO AIRPORT and RETURNING TO AIRPORT, which mean the same
    # thing and are never both the answer.
    "to airport": ("we are going back", "going back", "turning back",
                   "we are turning back", "heading back", "we are returning",
                   "back to the airport"),

    # --- announcements -----------------------------------------------------
    # "please listen" is deliberately absent: PLEASE LISTEN TO THE CABIN CREW
    # is a different announcement and already answers to it by name.
    "listen to instructions": ("listen carefully", "listen to me carefully",
                               "pay attention to the instructions"),
    "relax and enjoy": ("sit back and relax", "enjoy the flight", "sit back",
                        "enjoy your flight", "make yourselves comfortable"),

    # --- ending a call -----------------------------------------------------
    # "disconnect" on its own is left out: DISCONNECT JETWAY is a different
    # button, and losing that distinction would be worse than the gap.
    "hangup": ("hang up", "hang up the phone", "end the call",
               "disconnect the call"),

    # --- plain answers ---------------------------------------------------
    "yes": ("yes please", "yep", "yeah", "go for it", "permission granted",
            "granted", "approved"),
    "no": ("no thanks", "nope", "negative", "not now", "denied"),
    "not at the moment": ("not right now", "maybe later", "later"),
    "back": ("go back", "previous", "return", "cancel", "back up"),
}


# --------------------------------------------------------------------------
# The same table, in the language the pilot actually speaks.
#
# Everything above is English because of one setting: [stt] task = "translate"
# makes Whisper turn Polish into English before the matcher ever sees it, so
# the phrasings worth listing were whatever the translator happened to
# produce. That is a poor foundation. "mozecie tankowac" came back as
# "boarding", as "can you pass the bus to tank?", and as "you can refuel" on
# three different days, and each new spelling needed its own entry.
#
# BlueLine Realism does not do this. Its Polish pack maps Polish straight to
# the command - `CODE2_BACKUP | wsparc` - and never translates at all. The
# reason SLCVoiceAI could not was mechanical rather than deliberate:
# normalise() used to delete accented letters, so "mozecie" arrived as
# "mo ecie" and no Polish entry could ever have fired. intent.fold() fixes
# that, and this table is what it makes possible.
#
# With task = "transcribe" these are matched against what was really said.
# They stay harmless under "translate" - English in, English out, and none of
# this fires - so both settings work and neither needs the other.
#
# Written in the forms a captain actually uses: imperative, usually plural,
# addressed to the crew. Note that the coverage guard in intent.py compares
# whole words and Polish inflects them, so the singular and the plural of an
# order are separate entries, for the same reason "im listening" and "i am
# listening" are two strings above.
POLISH: dict[str, tuple[str, ...]] = {
    # --- acknowledgement -------------------------------------------------
    "roger": ("rozumiem", "zrozumiałem", "przyjąłem", "przyjęte", "jasne",
              "dobrze", "w porządku", "dobra", "okej", "przyjmuję",
              "dziękuję za informację", "dzięki za informację"),
    "will do": ("zrobi się", "wykonam", "zrobimy", "oczywiście"),
    "thank you": ("dziękuję", "dzięki", "dziękujemy", "dziękuję bardzo",
                  "wielkie dzięki", "dzięki wielkie"),
    "thanks very much": ("dziękuję bardzo", "bardzo dziękuję"),
    "no problem": ("nie ma sprawy", "nie ma problemu", "żaden problem",
                   "spoko"),
    "feeling fine": ("wszystko w porządku", "wszystko dobrze", "wszystko gra",
                     "czuję się dobrze", "bez zarzutu", "wszystko okej",
                     "u mnie w porządku", "wszystko jest w porządku"),

    # --- radio discipline ------------------------------------------------
    "go ahead": ("słucham", "mów", "mówcie", "możesz mówić", "możecie mówić",
                 "proszę mówić", "śmiało", "kontynuuj", "kontynuujcie",
                 "dawaj", "tak słucham", "słuchamy", "zamieniam się w słuch"),
    "standby": ("czekaj", "czekajcie", "poczekaj", "poczekajcie", "chwila",
                "chwileczkę", "moment", "momencik", "sekundę", "chwilę"),
    "repeat transmission": ("powtórz", "powtórzcie", "jeszcze raz",
                            "nie zrozumiałem", "nie dosłyszałem",
                            "możesz powtórzyć", "powtórz proszę"),
    "disregard": ("nieważne", "nieistotne", "zapomnij", "już nic",
                  "odwołuję", "nic takiego"),
    "radio check": ("próba radia", "sprawdzam radio", "kontrola radia",
                    "jak mnie słychać", "jak mnie słyszysz", "słyszysz mnie"),
    "loud and clear": ("głośno i wyraźnie", "pięć na pięć", "słyszę dobrze",
                       "dobrze cię słyszę", "czysto", "wyraźnie"),

    # --- opening a channel -----------------------------------------------
    "ground crew": ("obsługa naziemna", "załoga naziemna", "ziemia",
                    "kokpit do obsługi", "kokpit do ziemi", "do obsługi"),
    "intercom": ("interkom", "do kabiny", "kokpit do kabiny",
                 "łączę się z kabiną", "połącz z kabiną"),
    "purser to intercom": ("szef pokładu", "poproś szefa pokładu",
                           "stewardesa do interkomu", "szefa pokładu proszę",
                           "połącz mnie ze stewardesą"),
    "cabin crew to intercom": ("załoga do interkomu", "obsługa do interkomu"),
    "p a system": ("ogłoszenie", "do pasażerów", "system nagłośnienia",
                   "chcę ogłosić", "mówię do pasażerów", "nagłośnienie"),
    "phone": ("telefon", "dzwonię", "zadzwoń"),

    # --- the turnaround --------------------------------------------------
    "start boarding": ("zaczynajcie boarding", "zaczynamy boarding",
                       "możecie wpuszczać pasażerów", "wpuszczajcie pasażerów",
                       "rozpocznijcie boarding", "możecie zaczynać boarding"),
    "start boarding when ready": ("boarding kiedy będziecie gotowi",
                                  "zaczynajcie jak będziecie gotowi"),
    "ready to start boarding": ("gotowi do boardingu", "gotowy na boarding"),
    "request loading update": ("jak idzie załadunek", "status załadunku",
                               "jak wygląda załadunek", "co z załadunkiem"),
    "request offloading update": ("jak idzie rozładunek",
                                  "co z rozładunkiem"),
    "open the doors": ("otwórzcie drzwi", "otwórz drzwi", "możecie otworzyć"),
    "please close the doors": ("zamknijcie drzwi", "zamknij drzwi",
                               "możecie zamykać drzwi"),
    "connect jetway": ("podłączcie rękaw", "podłącz rękaw", "rękaw proszę",
                       "podstawcie rękaw", "podłączcie most"),
    "disconnect jetway": ("odłączcie rękaw", "odłącz rękaw",
                          "zabierzcie rękaw", "możecie zabrać rękaw"),
    "connect stairs": ("podstawcie schody", "podłączcie schody",
                       "schody proszę"),
    "disconnect stairs": ("zabierzcie schody", "odsuńcie schody"),
    "start catering": ("możecie zacząć catering", "catering proszę",
                       "zaczynajcie catering", "dostawa cateringu"),
    "start refuelling": ("możecie tankować", "tankujcie",
                         "zaczynajcie tankowanie", "rozpocznijcie tankowanie",
                         "prośba o tankowanie", "możecie zacząć tankowanie",
                         "zatankujcie", "proszę o tankowanie"),
    "start deboarding": ("możecie wysadzać", "zaczynajcie wysadzanie",
                         "wysadzamy pasażerów"),
    "start deicing": ("możecie odladzać", "odladzanie",
                      "zaczynajcie odladzanie"),

    # --- getting moving --------------------------------------------------
    "ready for pushback": ("gotowi do wypychania", "gotowy do pushbacku",
                           "gotowi do pushbacku"),
    "start pushback": ("wypychajcie", "zaczynajcie wypychanie",
                       "możecie wypychać"),
    "stop pushback": ("zatrzymajcie wypychanie", "stop wypychanie"),
    "parking brake released": ("hamulec zwolniony", "zwolniłem hamulec",
                               "hamulec postojowy zwolniony"),
    "parking brake is set": ("hamulec zaciągnięty", "zaciągnąłem hamulec",
                             "hamulec postojowy zaciągnięty",
                             "hamulec ustawiony"),
    "ready to go now": ("jesteśmy gotowi", "gotowi do drogi", "możemy jechać"),
    "starting the apu": ("uruchamiam apu", "włączam apu", "startuję apu"),
    "hotel startup": ("uruchomienie na hotelu", "start na hotelu"),
    "please disconnect gpu": ("odłączcie zasilanie", "odłączcie gpu",
                              "możecie odłączyć zasilanie"),
    "single engine taxi": ("kołowanie na jednym silniku",
                           "jeden silnik do kołowania"),
    "release the cabin crew": ("zwalniam załogę", "załoga może wstać",
                               "możecie wstać", "zwalniam obsługę"),

    # --- the cabin -------------------------------------------------------
    # SLC offers both of these, and they mean nearly the same thing, so
    # the two sets of words are kept apart on purpose: token_set_ratio
    # scores a subset as a perfect match, so one shared phrase in both
    # families ties them at 1.00 and the command is refused. Asking for
    # places goes to one, telling people to sit goes to the other.
    "seats for takeoff": ("zajmijcie miejsca", "zajmijcie swoje miejsca",
                          "miejsca do startu", "prosze zajac miejsca",
                          "przygotujcie się do startu"),
    "be seated for takeoff": ("siadajcie", "usiądźcie", "zaraz startujemy",
                              "siadajcie do startu", "usiądźcie do startu",
                              "startujemy"),
    "take seats for landing": ("zajmijcie miejsca do lądowania",
                               "siadajcie do lądowania", "zaraz lądujemy"),
    "be seated for landing": ("usiądźcie do lądowania",
                              "lądujemy zajmijcie miejsca"),
    "prepare cabin for landing": ("przygotujcie kabinę do lądowania",
                                  "kabina do lądowania",
                                  "przygotujcie kabinę"),
    "welcome aboard": ("witamy na pokładzie", "dzień dobry państwu",
                       "witam na pokładzie"),
    "seatbelts": ("pasy", "zapnijcie pasy", "włączam pasy",
                  "sygnał zapiąć pasy"),
    "toggle doors": ("przełącz drzwi",),
    "please be seated": ("proszę usiąść", "proszę zająć miejsca"),
    "please remain seated": ("proszę pozostać na miejscach",
                             "proszę nie wstawać"),
    "relax and enjoy": ("życzę miłego lotu", "miłego lotu",
                        "życzymy miłego lotu"),
    "listen to instructions": ("proszę słuchać instrukcji",
                               "słuchajcie instrukcji"),

    # --- the flight ------------------------------------------------------
    "descent starting": ("zaczynamy zniżanie", "rozpoczynamy zniżanie",
                         "schodzimy", "zaczynamy schodzić"),
    "descending soon": ("niedługo zaczniemy zniżanie", "wkrótce zniżanie"),
    "descending shortly": ("za chwilę zaczniemy zniżanie",
                           "zaraz zaczniemy zniżanie"),
    "started our descent": ("zaczęliśmy zniżanie", "jesteśmy w zniżaniu"),
    "to airport": ("na lotnisko", "do lotniska"),
    "perfect": ("świetnie", "doskonale", "super", "idealnie"),
    "hows it going": ("jak leci", "jak tam", "jak idzie"),
    "how are the passengers": ("jak pasażerowie", "jak się mają pasażerowie",
                               "co u pasażerów"),

    # --- delays ----------------------------------------------------------
    "sorry for the delay": ("przepraszam za opóźnienie",
                            "przepraszamy za opóźnienie"),
    "apologies": ("przepraszam", "przepraszamy"),
    "thats my bad": ("moja wina", "mój błąd"),
    "were running behind": ("jesteśmy opóźnieni", "mamy opóźnienie"),
    "atc delay": ("opóźnienie od kontroli", "kontrola nas trzyma"),
    "no departure delay": ("bez opóźnienia", "startujemy o czasie"),
    "short delay expected": ("krótkie opóźnienie", "niewielkie opóźnienie"),
    "medium delay expected": ("średnie opóźnienie",),
    "extended delay expected": ("duże opóźnienie", "dłuższe opóźnienie"),

    # --- plain answers ---------------------------------------------------
    "yes": ("tak", "oczywiście", "zgoda", "pozwalam", "zezwalam"),
    "no": ("nie", "niestety nie", "odmawiam"),
    "not at the moment": ("nie teraz", "na razie nie", "może później"),
    "hangup": ("rozłączam", "kończę", "rozłączam się"),
    "back": ("wstecz", "cofnij", "wróć", "powrót"),
}

# One table from here on. Polish entries are appended rather than replacing
# anything, so a button keeps every English phrasing it had: the pilot may
# switch [stt] task between "transcribe" and "translate", and half a flight
# can arrive in each if listening is switched off and on.
for _key, _forms in POLISH.items():
    ALIASES[_key] = ALIASES.get(_key, ()) + tuple(_forms)

# A second pass, written against the buttons SLC actually offered across the
# logged flights rather than against the English table above - the first pass
# covered 45 of the 100, and the ones it missed are not rare: HELLO?, the
# engine start calls, the cabin service requests, every arrival-time phrase.
# A gap here is not a slower command, it is a refused one, because the
# cascade no longer asks the cloud when nothing came close.
POLISH_MORE: dict[str, tuple[str, ...]] = {
    # --- opening a conversation ------------------------------------------
    "hello": ("halo", "dzień dobry", "jest tam kto", "słyszycie mnie",
              "kokpit"),
    "thanks": ("dzięki", "dziękuję"),
    "forgot what i needed": ("zapomniałem czego chciałem",
                             "już nie pamiętam", "nieważne zapomniałem"),
    "ill speak to you later": ("pogadamy później", "odezwę się później"),
    "ill call when were ready": ("zadzwonię jak będziemy gotowi",
                                 "odezwę się jak będziemy gotowi"),

    # --- the flight deck --------------------------------------------------
    "starting engine 1": ("uruchamiam pierwszy silnik",
                          "startuję pierwszy silnik", "silnik jeden"),
    "starting engine 2": ("uruchamiam drugi silnik",
                          "startuję drugi silnik", "silnik dwa"),
    "cockpit secured": ("kokpit zabezpieczony", "zabezpieczyłem kokpit"),
    "awaiting the loadsheet": ("czekam na loadsheet", "czekamy na loadsheet",
                               "czekam na dokumenty"),
    "pushback not required": ("wypychanie niepotrzebne",
                              "nie potrzebujemy wypychania"),
    "instant boarding": ("boarding od razu", "natychmiastowy boarding"),
    "clear to deboard": ("można wysadzać", "możecie wysadzać pasażerów",
                         "zgoda na wysadzanie"),

    # --- the slide pins ---------------------------------------------------
    # SLC shows these as "THANKS, PIN LEFT" / "THANKS, PIN RIGHT" once the
    # doors are disarmed; the pilot is acknowledging one side at a time.
    "pin left": ("zawleczka z lewej", "lewa zawleczka", "lewa strona"),
    "pin right": ("zawleczka z prawej", "prawa zawleczka", "prawa strona"),
    "armed": ("uzbrojone", "zazbrojone"),
    "closed": ("zamknięte",),
    "open": ("otwarte",),

    # --- the cruise -------------------------------------------------------
    "were climbing to cruise": ("wznosimy się na poziom przelotowy",
                                "wznosimy się", "idziemy na poziom"),
    "normal cruise": ("normalny przelot", "zwykły przelot",
                      "standardowy przelot"),
    "brief cruise": ("krótki przelot", "krótki lot"),

    # --- punctuality ------------------------------------------------------
    "were on time": ("jesteśmy o czasie", "lecimy o czasie"),
    "on schedule": ("zgodnie z planem", "zgodnie z rozkładem"),
    "a little delayed": ("lekko opóźnieni", "trochę spóźnieni"),
    "running late": ("spóźniamy się", "jesteśmy spóźnieni"),

    # --- time to go -------------------------------------------------------
    # Separate keys per number: the matcher compares whole words, so "za
    # dwadziescia minut" must not be allowed to score against the ten-minute
    # button on the strength of "za" and "minut".
    "5 mins until descent": ("pięć minut do zniżania",),
    "10 mins until descent": ("dziesięć minut do zniżania",),
    "15 mins until descent": ("piętnaście minut do zniżania",),
    "20 mins until descent": ("dwadzieścia minut do zniżania",),
    "25 mins until descent": ("dwadzieścia pięć minut do zniżania",),
    "30 mins until descent": ("trzydzieści minut do zniżania",),
    "arriving in 10 minutes": ("lądujemy za dziesięć minut",
                               "za dziesięć minut na miejscu"),
    "arriving in 15 minutes": ("lądujemy za piętnaście minut",),
    "arriving in 20 minutes": ("lądujemy za dwadzieścia minut",),
    "arriving in 30 minutes": ("lądujemy za trzydzieści minut",
                               "za pół godziny lądujemy"),
    "arriving in 40 minutes": ("lądujemy za czterdzieści minut",),

    # --- the cabin looking after the pilot --------------------------------
    "can i have some coffee": ("poproszę kawę", "kawa poproszę",
                               "chętnie napiłbym się kawy"),
    "can i have some tea": ("poproszę herbatę", "herbata poproszę"),
    "can i have some water": ("poproszę wodę", "woda poproszę",
                              "poproszę o wodę"),
    "can i have a snack": ("poproszę coś do jedzenia", "poproszę przekąskę"),

    # --- the music --------------------------------------------------------
    "turn the music on": ("włączcie muzykę", "włącz muzykę"),
    "turn the music off": ("wyłączcie muzykę", "wyłącz muzykę"),
    "turn the music up": ("głośniej muzykę", "podgłośnijcie muzykę"),
    "turn the music down": ("ciszej muzykę", "ściszcie muzykę"),
    "up a bit more": ("jeszcze trochę głośniej", "troszkę głośniej"),
    "down a bit more": ("jeszcze trochę ciszej", "troszkę ciszej"),

    # --- announcements ----------------------------------------------------
    "ladies and gentlemen": ("szanowni państwo", "drodzy państwo",
                             "panie i panowie", "witam państwa"),

    # --- the jetway, from the ground crew's side --------------------------
    "ill ask for a jetway": ("poproszę o rękaw", "zamówię rękaw"),
    "ill remove the jetway": ("zabiorę rękaw", "usunę rękaw"),
    # One of the three toolbar icons that survive is_chrome, because it
    # changes the aircraft rather than the panel: jetway or stairs.
    "toggle door mode": ("tryb schodów", "przełącz na schody",
                         "tryb rękawa", "przełącz na rękaw"),
}

for _key, _forms in POLISH_MORE.items():
    POLISH[_key] = POLISH.get(_key, ()) + tuple(_forms)
    ALIASES[_key] = ALIASES.get(_key, ()) + tuple(_forms)


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


def target_not_offered(said: str, button_names) -> str | None:
    """The button the pilot was reaching for, when it is not on screen.

    `aliases_for` asks what a button can be called. This asks the reverse -
    what was the pilot reaching for - and answers only when that button is
    not among the ones SLC is showing.

    It exists because of what a refusal looks like from the cockpit. Saying
    "go ahead" to a ground crew that is not waiting gets "the phrase is too
    ambiguous", which blames the wording and is simply untrue: the wording
    was exact, the button was absent. One flight lost eight minutes to that
    message, tried in two languages.

    Returns None when the phrase belongs to no family, or when the family's
    button is on screen after all - then the refusal really was about
    matching, and whatever the matcher said about it stands.
    """
    words = _words(said)
    if not words:
        return None

    best_key, best_len = None, 0
    for key, phrases in ALIASES.items():
        for phrase in (key,) + phrases:
            needle = _words(phrase)
            if len(needle) <= best_len or not _contains_sequence(words, needle):
                continue
            # The phrase has to be most of what was said. "ok" is an alias
            # for ROGER and sits inside half of everything a pilot says, so
            # without this "ok, you can start boarding if you are ready"
            # would be reported as reaching for a button nobody wanted.
            if len(words) > len(needle) + 2:
                continue
            best_key, best_len = key, len(needle)

    if best_key is None:
        return None
    wanted = _words(best_key)
    for name in button_names:
        if _contains_sequence(_words(name), wanted):
            return None
    return best_key.upper()


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
