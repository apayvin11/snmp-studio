# SPDX-License-Identifier: Apache-2.0
"""Bounded BER inspection of actual SNMP datagrams, including field offsets."""
from dataclasses import dataclass, field

TAGS = {2:'INTEGER', 4:'OCTET STRING', 5:'NULL', 6:'OID', 48:'SEQUENCE',
        64:'IpAddress', 65:'Counter32', 66:'Gauge32', 67:'TimeTicks', 68:'Opaque', 70:'Counter64',
        128:'noSuchObject', 129:'noSuchInstance', 130:'endOfMibView',
        160:'GET', 161:'GETNEXT', 162:'RESPONSE', 163:'SET', 164:'TRAPv1',
        165:'GETBULK', 166:'INFORM', 167:'TRAP', 168:'REPORT'}


@dataclass
class Field:
    tag: int
    start: int
    value_start: int
    end: int
    label: str = ''
    value: str = ''
    children: list = field(default_factory=list)
    sensitive: bool = False


def decode_oid(data):
    arcs, acc, open_arc = [], 0, False
    for byte in data:
        acc = (acc << 7) | (byte & 127)
        open_arc = bool(byte & 128)
        if acc.bit_length() > 64:
            raise ValueError('OID arc too large')
        if not open_arc:
            arcs.append(acc)
            acc = 0
    if open_arc or not arcs:
        raise ValueError('Truncated OID')
    first = arcs.pop(0)
    leading = 0 if first < 40 else 1 if first < 80 else 2
    return '.'.join(map(str, [leading, first-leading*40]+arcs))


def inspect_packet(raw):
    count = 0
    def parse(start, limit, depth=0):
        nonlocal count
        count += 1
        if depth > 24 or count > 8192 or start+2 > limit:
            raise ValueError('BER nesting / size / truncated header')
        tag, pos = raw[start], start+1
        if tag & 31 == 31:
            raise ValueError('Unsupported BER high tag')
        length = raw[pos]
        pos += 1
        if length & 128:
            n = length & 127
            if not 1 <= n <= 4 or pos+n > limit:
                raise ValueError('Invalid BER length')
            length = int.from_bytes(raw[pos:pos+n], 'big')
            pos += n
        end = pos+length
        if end > limit:
            raise ValueError('Truncated BER value')
        node = Field(tag, start, pos, end, TAGS.get(tag, f'Tag 0x{tag:02X}'))
        data = raw[pos:end]
        if tag & 32:
            child = pos
            while child < end:
                field = parse(child, end, depth+1)
                node.children.append(field)
                child = field.end
        elif tag in (2, 65, 66, 67, 70):
            node.value = str(int.from_bytes(data, 'big', signed=tag == 2))
        elif tag == 6:
            node.value = decode_oid(data)
        elif tag == 64:
            node.value = '.'.join(map(str, data))
        elif tag == 4:
            node.value = data.decode('utf-8', 'backslashreplace')
        else:
            node.value = data.hex(' ').upper()
        return node
    if len(raw) > 65535:
        raise ValueError('Datagram too large')
    root = parse(0, len(raw))
    if root.end != len(raw):
        raise ValueError('Trailing bytes after SNMP message')
    root.label = 'SNMP'
    children = root.children
    def label(nodes, labels):
        for node, name in zip(nodes, labels):
            node.label = name
    if len(children) >= 3 and children[0].tag == 2:
        children[0].label = 'Version'
        version = int(children[0].value)
        if version in (0, 1):
            children[1].label, children[1].sensitive = 'Community', True
            pdu = children[2]
        elif version == 3 and len(children) == 4:
            label(children, ('Version','Header','USM security','Scoped PDU'))
            label(children[1].children, ('Message ID','Max size','Flags','Security model'))
            if children[2].tag == 4:
                try:
                    usm = parse(children[2].value_start, children[2].end)
                    label(usm.children, ('Engine ID','Engine boots','Engine time','Username','Authentication','Privacy parameters'))
                    children[2].children = [usm]
                    children[2].value = ''
                except ValueError:
                    pass
            if children[3].tag == 4:
                children[3].label, children[3].value = 'Encrypted scoped PDU', raw[children[3].value_start:children[3].end].hex(' ').upper()
                return root
            label(children[3].children, ('Context engine ID','Context name','PDU'))
            pdu = children[3].children[2] if len(children[3].children) == 3 else None
        else:
            pdu = None
        if pdu:
            pdu.label = TAGS.get(pdu.tag, 'PDU')
            label(pdu.children, ('Request ID', 'Non-repeaters' if pdu.tag == 165 else 'Error status', 'Max repetitions' if pdu.tag == 165 else 'Error index', 'VarBind list'))
            if pdu.tag == 164:
                label(pdu.children, ('Enterprise OID','Agent address','Generic trap','Specific trap','TimeTicks','VarBind list'))
            varlist = pdu.children[-1] if pdu.children else None
            if varlist and varlist.tag == 48:
                for var in varlist.children:
                    if len(var.children) == 2:
                        oid, value = var.children
                        oid.label = 'OID'
                        value.label = f'{TAGS.get(value.tag, "Value")} · {oid.value}'
                        var.label = oid.value
    return root


def flatten(node):
    yield node
    for child in node.children:
        yield from flatten(child)


def safe_hex(raw, reveal=False):
    hidden = set()
    if not reveal:
        try:
            for node in flatten(inspect_packet(raw)):
                if node.sensitive:
                    hidden.update(range(node.value_start, node.end))
        except ValueError:
            # Invalid datagrams may contain credentials: no raw export by default.
            return '[malformed datagram: raw bytes hidden]'
    return ' '.join('••' if i in hidden else f'{byte:02X}' for i, byte in enumerate(raw))
