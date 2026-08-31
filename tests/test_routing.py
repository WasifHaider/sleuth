from sleuth.retrieve.routing import classify_question


def test_classifies_architecture_question_as_global():
    assert classify_question("Rate my architecture") == "global"


def test_classifies_summarize_whole_project_as_global():
    assert classify_question("Can you summarize the whole project?") == "global"


def test_classifies_specific_function_question_as_local():
    assert classify_question("Where is create_repo implemented?") == "local"


def test_classifies_find_every_place_as_local_not_covered_by_summary():
    # "find every place we do X" needs Phase 3 (agentic global mode), not
    # the Phase 1 summary — the source plan explicitly calls this out as
    # NOT what the summary artifact covers, so it must stay local for now.
    assert classify_question("Find every place we call requests.get") == "local"


def test_is_case_insensitive():
    assert classify_question("WHAT IS THE OVERALL ARCHITECTURE?") == "global"
