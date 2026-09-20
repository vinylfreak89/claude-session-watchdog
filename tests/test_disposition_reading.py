"""Closing a turn is answerable in natural language, but only by quoting the candidate.

Owner's ruling, 2026-09-20: "as everything else it should be answerable by natural
language." The old guard matched question punctuation anywhere in a turn, so the target
narrating its own next measurement ("does any real placement put field 1's rows past the
padding...?") held the turn owed forever.

The teeth that replace the block: candidates are re-derived from the transcript, a
disposition needs a reading, and the reading must carry each candidate's OWN WORDS. A
blanket dismissal disposes of nothing.

Every fixture here is synthesised, so nothing outside this file can silence it.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wd_turns as TD


class FakeTurn:
    def __init__(self, texts):
        self.assistant_texts = [(str(i), t) for i, t in enumerate(texts)]
        self.tool_uses = []
        self.records = [{'t': t} for t in texts]
        self.end_state = 'end_turn'


RHETORICAL = ("While the tests run, I'm quantifying the minor bound finding: does any real "
              "placement put field 1's rows past the padding into field 2's lines?")
REAL = "Which bar should I use, 4.5 or 20? I cannot proceed until you say."
PLAIN = "The validation script is ready. It will check every frame's placement and the audio length."


def main():
    # 1. A turn with no question-shaped sentence yields no candidates at all.
    cands, hard = TD.question_candidates(FakeTurn([PLAIN]))
    assert hard is None, hard
    assert cands == [], cands

    # 2. The rhetorical sentence IS found -- finding it is measurement, not judgement.
    cands, _ = TD.question_candidates(FakeTurn([RHETORICAL]))
    assert len(cands) == 1 and 'does any real placement' in cands[0], cands

    # 3. A blanket dismissal quotes nothing, so it disposes of nothing.
    for blanket in ('none of these are questions to me',
                    'rhetorical', '', 'the turn asked nothing of the watchdog'):
        missing = TD.unquoted_candidates(cands, blanket)
        assert missing == cands, 'blanket %r was accepted: %r' % (blanket, missing)

    # 4. A reading carrying the candidate's own words disposes of it.
    reading = ("The only question-shaped line is the target asking itself what it is about to "
               "measure: \"While the tests run, I'm quantifying the minor bound finding: does any "
               "real placement put field 1's rows past the padding into field 2's lines?\" -- that "
               "is narration of its next step, not a question to the watchdog.")
    assert TD.unquoted_candidates(cands, reading) == [], TD.unquoted_candidates(cands, reading)

    # 5. A REAL question is not disposed of by quoting a DIFFERENT candidate: each one
    #    must be carried, so a turn mixing narration and a real ask cannot ride through
    #    on the narration's coat-tails.
    both, _ = TD.question_candidates(FakeTurn([RHETORICAL, REAL]))
    assert len(both) >= 2, both
    missing = TD.unquoted_candidates(both, reading)
    assert missing, 'the real question was dismissed by a reading that never quoted it'
    assert any('4.5 or 20' in m for m in missing), missing

    # 6. Unreadable text is a measurement failure: no reading disposes of it.
    cands, hard = TD.question_candidates(FakeTurn([None]))
    assert hard == 'unreadable request text', (cands, hard)

    # 7. The conservative boolean used by AUTOMATIC supersession still fires on both,
    #    since nobody is reading there.
    assert TD.has_question_candidates(FakeTurn([RHETORICAL]))
    assert TD.has_question_candidates(FakeTurn([REAL]))
    assert not TD.has_question_candidates(FakeTurn([PLAIN]))
    print('ok')


if __name__ == '__main__':
    main()
