from app.asr import merge_transcripts


def test_merge_transcripts_trims_and_joins():
    result = merge_transcripts([" hello", "world", "", "!  \n"])
    assert result == "hello world !"
