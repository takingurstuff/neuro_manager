from mutagen.id3 import ID3

audio = ID3(
    "/home/talent/neuro/DISC 1 - Humble Beginnings (2023-01-03 - 2023-05-17)/015. Imagine Dragons, JID - Enemy (Neuro.v1).mp3"
)

# The key format is usually 'COMM:description:lang'
# If there is NO description, it's just 'COMM::lang'
key = "COMM::ved"

try:
    comm_frame = audio[key]
    json_str = comm_frame.text[0]
    print(f"Metadata found: {json_str}")
except KeyError:
    print("Frame not found with that exact key.")
