# DAWproject schema

`Project.xsd` and `MetaData.xsd` are vendored verbatim from
https://github.com/bitwig/dawproject (MIT License, Copyright (c) 2020 Bitwig),
copied from `~/video/schema/dawproject/`.

`src/dawproject_writer.py` validates every file it writes against `Project.xsd`.
Some DAWs silently accept invalid values, so the schema check is the one place such
mistakes show up.
