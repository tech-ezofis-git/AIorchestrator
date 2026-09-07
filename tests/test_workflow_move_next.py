from app.ap_skills.workflow_move_next import form_entry_id_for_v6_move_next


def test_form_entry_id_for_v6_move_next_keeps_integers():
    assert form_entry_id_for_v6_move_next("42") == 42
    assert form_entry_id_for_v6_move_next(42) == 42


def test_form_entry_id_for_v6_move_next_keeps_guids():
    guid = "113c8e90-8f5d-488d-8938-0c9bdf2a2928"
    assert form_entry_id_for_v6_move_next(guid) == guid
    assert form_entry_id_for_v6_move_next(guid.upper()) == guid
    assert form_entry_id_for_v6_move_next(None) is None
