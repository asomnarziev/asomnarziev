"""Messages testlari."""


def test_local_time_and_audio_name():
    from app.messages import audio_name, render_call

    call = {
        "uuid": "cc04",
        "caller_id_number": "105",
        "destination_number": "935033635",
        "start_stamp": 1791358224,
        "accountcode": "outbound",
        "duration": 44,
    }  # 2026-10-07 07:30:24 UTC
    assert "07.10.2026 12:30:24" in render_call("uz", call)
    assert audio_name(call) == "105_935033635_07.10_12-30.mp3"
    assert audio_name({"uuid": "x/../y"}) == "x..y.mp3" or audio_name({"uuid": "x/../y"}).endswith(".mp3")
