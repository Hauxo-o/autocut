"""Verifie un XML genere : syntaxe, synchro video/audio, calcul des durees."""
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
seq = root.find("sequence")
seq_dur = int(seq.findtext("duration"))


def spans(track):
    return [(int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in")), int(c.findtext("out")))
            for c in track.findall("clipitem")]


v = spans(seq.find("media/video/track"))
audio_tracks = [spans(t) for t in seq.findall("media/audio/track")]
print("clips video:", len(v), "| pistes audio:", len(audio_tracks), "| duree timeline (images):", seq_dur)
assert all(t == v for t in audio_tracks), "ERREUR : audio pas identique a la video"
pos = 0
for s, e, i, o in v:
    assert e - s == o - i, "duree incoherente"
    assert s >= pos, "chevauchement"
    pos = e
assert pos <= seq_dur
print("OK : video et", len(audio_tracks), "pistes audio coupees aux memes images, sans chevauchement")
print("marqueurs:", len(seq.findall("marker")))
