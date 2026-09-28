from sentinel.verify.equivalence import behaviour_preserving

OLD = '''class Report:
    def percentage(self, records, digits=1):
        present = sum(1 for r in records if r.present)
        return round(100.0 * present / len(records), digits)
'''


def test_renamed_locals_and_docstrings_are_preserving():
    renamed = OLD.replace("present = ", "attended = ").replace("* present /", "* attended /")
    documented = renamed.replace("digits=1):\n", 'digits=1):\n        """Share of sessions attended."""\n')
    assert behaviour_preserving("Report.percentage", OLD, renamed)
    assert behaviour_preserving("Report.percentage", OLD, documented)


def test_real_changes_are_not():
    assert not behaviour_preserving("Report.percentage", OLD, OLD.replace("100.0", "10.0"))
    assert not behaviour_preserving("Report.percentage", OLD, OLD.replace("r.present)", "r.late)"))  # attribute, not a local
    assert not behaviour_preserving("Report.percentage", OLD, OLD.replace("digits=1", "digits=2"))
    assert not behaviour_preserving("Report.missing", OLD, OLD)
