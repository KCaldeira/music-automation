"""Write note tracks as a DAWproject (.dawproject) file for Cubase.

A .dawproject is a ZIP holding project.xml and metadata.xml. Each track here is
an instrument ("notes") track holding one clip of notes; times are in beats
(quarter notes), so the clips follow the project tempo. The structure follows
~/video/curve_to_dawproject.py, which writes audio/automation tracks; note clips
are new here. Every project.xml is validated against the vendored schema.
"""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.dom import minidom

import xmlschema

DAWPROJECT_VERSION = "1.0"
APPLICATION_NAME = "music-automation"
APPLICATION_VERSION = "1.0"
PROJECT_XSD = Path(__file__).resolve().parent.parent / "schema" / "dawproject" / "Project.xsd"


class IdFactory:
    """Sequential XML ids (id0, id1, ...), the convention the format uses."""

    def __init__(self):
        self.count = 0

    def next(self):
        value = f"id{self.count}"
        self.count += 1
        return value


def _real_parameter(parent, tag, value, unit, minimum, maximum, name, ids):
    element = ET.SubElement(parent, tag)
    element.set("max", f"{maximum:.6f}")
    element.set("min", f"{minimum:.6f}")
    element.set("unit", unit)
    element.set("value", f"{value:.6f}")
    element.set("id", ids.next())
    element.set("name", name)
    return element


def _channel(parent, ids, role, destination=None):
    channel = ET.SubElement(parent, "Channel")
    channel.set("audioChannels", "2")
    if destination is not None:
        channel.set("destination", destination)
    channel.set("role", role)
    channel.set("solo", "false")
    channel.set("id", ids.next())
    mute = ET.SubElement(channel, "Mute")
    mute.set("value", "false")
    mute.set("id", ids.next())
    mute.set("name", "Mute")
    _real_parameter(channel, "Pan", 0.5, "normalized", 0.0, 1.0, "Pan", ids)
    _real_parameter(channel, "Volume", 1.0, "linear", 0.0, 2.0, "Volume", ids)
    return channel


def build_project_xml(tracks, tempo_bpm, numerator, denominator):
    """tracks: list of (name, length_beats, notes); notes are
    (time_beats, duration_beats, midi_key, velocity 0-127)."""
    ids = IdFactory()
    root = ET.Element("Project")
    root.set("version", DAWPROJECT_VERSION)
    application = ET.SubElement(root, "Application")
    application.set("name", APPLICATION_NAME)
    application.set("version", APPLICATION_VERSION)

    transport = ET.SubElement(root, "Transport")
    _real_parameter(transport, "Tempo", tempo_bpm, "bpm", 20.0, 666.0, "Tempo", ids)
    signature = ET.SubElement(transport, "TimeSignature")
    signature.set("denominator", str(denominator))
    signature.set("numerator", str(numerator))
    signature.set("id", ids.next())

    structure = ET.SubElement(root, "Structure")
    note_tracks = []
    for name, _, _ in tracks:
        track = ET.SubElement(structure, "Track")
        track.set("contentType", "notes")
        track.set("loaded", "true")
        track.set("id", ids.next())
        track.set("name", name)
        note_tracks.append(track)
    master = ET.SubElement(structure, "Track")
    master.set("contentType", "audio")
    master.set("loaded", "true")
    master.set("id", ids.next())
    master.set("name", "Master")
    master_id = _channel(master, ids, "master").get("id")
    for track in note_tracks:
        _channel(track, ids, "regular", destination=master_id)

    arrangement = ET.SubElement(root, "Arrangement")
    arrangement.set("id", ids.next())
    outer = ET.SubElement(arrangement, "Lanes")
    outer.set("timeUnit", "beats")
    outer.set("id", ids.next())
    for track, (name, length_beats, notes) in zip(note_tracks, tracks):
        lanes = ET.SubElement(outer, "Lanes")
        lanes.set("track", track.get("id"))
        lanes.set("id", ids.next())
        clips = ET.SubElement(lanes, "Clips")
        clips.set("id", ids.next())
        clip = ET.SubElement(clips, "Clip")
        clip.set("time", "0.0")
        clip.set("duration", f"{length_beats:.6f}")
        clip.set("contentTimeUnit", "beats")
        clip.set("playStart", "0.0")
        clip.set("name", name)
        notes_el = ET.SubElement(clip, "Notes")
        notes_el.set("id", ids.next())
        for time, duration, key, velocity in notes:
            note = ET.SubElement(notes_el, "Note")
            note.set("time", f"{time:.6f}")
            note.set("duration", f"{duration:.6f}")
            note.set("channel", "0")
            note.set("key", str(key))
            note.set("vel", f"{velocity / 127:.6f}")
            note.set("rel", f"{velocity / 127:.6f}")

    ET.SubElement(root, "Scenes")
    return root


def _serialize(element):
    raw = ET.tostring(element, encoding="utf-8")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8")


def _validate(project_xml):
    errors = list(xmlschema.XMLSchema(str(PROJECT_XSD)).iter_errors(project_xml.decode("utf-8")))
    if errors:
        detail = "\n".join(f"  {e.reason}" for e in errors[:5])
        raise ValueError(f"project.xml is not schema-valid ({len(errors)} error(s)):\n{detail}")


def write_dawproject(path, tracks, tempo_bpm, numerator, denominator, title, comment):
    project_xml = _serialize(build_project_xml(tracks, tempo_bpm, numerator, denominator))
    _validate(project_xml)
    metadata = ET.Element("MetaData")
    ET.SubElement(metadata, "Title").text = title
    ET.SubElement(metadata, "Comment").text = comment
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project.xml", project_xml, zipfile.ZIP_DEFLATED)
        archive.writestr("metadata.xml", _serialize(metadata), zipfile.ZIP_DEFLATED)
