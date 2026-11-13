"""Consistent block names and colors across dispatch and the map."""
PALETTE = (
    ('Blue', '#2563eb'), ('Orange', '#ea580c'), ('Green', '#15803d'),
    ('Purple', '#9333ea'), ('Red', '#dc2626'), ('Teal', '#0d9488'),
    ('Pink', '#db2777'), ('Gold', '#a16207'),
)


def block_style(config, block):
    readers = config['readers']
    index = next(i for i, reader in enumerate(readers) if reader['id'] == block)
    name, color = PALETTE[index % len(PALETTE)]
    cycle = index // len(PALETTE)
    return (f'{name} {cycle + 1}' if cycle else name), color


def block_name(config, block):
    return block_style(config, block)[0] + ' block'
