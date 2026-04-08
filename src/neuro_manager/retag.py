import json
from io import BytesIO
from logging import Logger
from aiopath import AsyncPath
from mutagen.id3 import ID3, TPE1, TALB, TRCK, TPOS, TDRC, COMM, TPE2, TPE4


async def tag_mp3(
    content: bytes,
    save_path: AsyncPath,
    logger: Logger,
):
    logger.info(f"Saving to {save_path}")
    af = BytesIO(content)
    try:
        tags = ID3(af)
    except:
        logger.warning("Probably not MP3, saving anyways")
        await save_path.write_bytes(content)
        return
    logger.debug(f"Embedded JSON Metadata: {tags["COMM::ved"].text[0]}")
    try:
        meta = json.loads(tags["COMM::ved"].text[0])
    except json.JSONDecodeError:
        logger.warning("Bad embedded metadata found, saving anyways")
        await save_path.write_bytes(content)
        return
    tags["TPE1"] = TPE1(
        encoding=3,
        text=meta["Artist"].split(", ")
        + meta["CoverArtist"].split(" & ")
        + ["QueenPb", "vedal987"],
    )
    tags["TPE2"] = TPE2(encoding=3, text=["Neuro", "Evil"])
    tags["TPE4"] = TPE4(encoding=3, text=["QueenPb", "vedal987"])
    tags["TALB"] = TALB(encoding=3, text=["Neuro-sama Karaoke Covers"])
    tags["TPOS"] = TPOS(encoding=3, text=[meta["Discnumber"]])
    tags["TRCK"] = TRCK(encoding=3, text=[meta["Track"]])
    tags["COMM"] = COMM(
        encoding=3, text=["Modified by Neuro-Karaoke-Manager with mutagen"]
    )
    tags["TDRC"] = TDRC(encoding=3, text=[meta["Date"]])
    af.seek(0)
    tags.save(af)
    await save_path.write_bytes(af.getvalue())
