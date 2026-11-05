# SPDX-License-Identifier: Apache-2.0
"""Whitespace-only MIB formatting with four-space OID hierarchy and retained comments."""
from __future__ import annotations
from dataclasses import dataclass
from lark import Token
from pysmi.parser import SmiStarParser
from pysmi.parser import lark_parser
from .mib import BASE,MAX_MIB,parts,parse_source


class MibFormatError(ValueError):
    pass


CLAUSES={'SYNTAX','WRITE-SYNTAX','UNITS','MAX-ACCESS','MIN-ACCESS','ACCESS','STATUS','DESCRIPTION',
 'REFERENCE','INDEX','AUGMENTS','DEFVAL','OBJECTS','VARIABLES','ENTERPRISE','LAST-UPDATED','ORGANIZATION',
 'CONTACT-INFO','REVISION','DISPLAY-HINT','PRODUCT-RELEASE','SUPPORTS','INCLUDES','VARIATION',
 'CREATION-REQUIRES','MANDATORY-GROUPS','GROUP','OBJECT','MODULE'}


@dataclass
class Declaration:
    start: int
    header_end: int
    kind: str
    depth: int=0


class Writer:
    def __init__(self):self.lines=[];self.line='';self.last='';self.indent=0
    def flush(self):
        if self.line:self.lines.append(self.line);self.line=''
        self.last=''
    def blank(self):
        self.flush()
        if self.lines and self.lines[-1]!='':self.lines.append('')
    def atom(self,value,indent):
        if not self.line:self.line=' '*indent;self.indent=indent;self.last=''
        tight=value in (')',']',',',';','..') or self.last in ('(','[','..')
        if value=='(' and self.last and self.last[0].islower() and self.last not in ('SIZE',):tight=True
        self.line+=('' if self.last=='' or tight else ' ')+value;self.last=value
    def comment(self,value,indent,inline=False):
        if inline and self.line:self.line+=' '+value;self.flush()
        else:self.flush();self.lines.append(' '*indent+value)
    def result(self):
        self.flush()
        return '\n'.join(self.lines).rstrip('\n')+'\n'


def _lexical_source(parser,text):
    # PySMI skips legacy MACRO and CHOICE bodies. Mask them without changing offsets
    # for the syntax tree, and retain each original body as one opaque lexical item.
    masked=list(text);opaque=[]
    for expression in (lark_parser._LEGACY_MACRO_RE,lark_parser._LEGACY_CHOICE_RE,lark_parser._LEGACY_EXPORTS_RE):
        for match in expression.finditer(text):
            start=match.start('body');end=match.end('body')
            if expression is lark_parser._LEGACY_CHOICE_RE:end=match.end()
            for index in range(start,end):
                if masked[index] not in ('\n','\r'):masked[index]=' '
            opaque.append((match.start(),match.end()))
    normalized=''.join(masked);tokens=[];covered=set()
    for token in parser.parser.lex(normalized):
        region=next(((start,end) for start,end in opaque if start<=token.start_pos<end),None)
        if region:
            if region not in covered:
                start,end=region;tokens.append(Token('PRESERVED',text[start:end],start_pos=start,end_pos=end));covered.add(region)
        else:tokens.append(token)
    return normalized,tokens


def _declarations(parser,text,masked):
    tree=parser.parser.parse(masked)
    objects,_,errors=parse_source(text)
    if errors:raise MibFormatError('\n'.join(item.message for item in errors))
    by_name={(obj.module,obj.name):obj for obj in objects};result={};modules=[]
    for module_tree in tree.find_data('module'):
        name_tree=next(child for child in module_tree.children if getattr(child,'data',None)=='module_name')
        name_token=next(name_tree.scan_values(lambda item:isinstance(item,Token)));module=str(name_token)
        nodes=[]
        for declaration in module_tree.find_data('declaration'):
            token=min(declaration.scan_values(lambda item:isinstance(item,Token)),key=lambda item:item.start_pos,default=None)
            if token is None:continue
            obj=by_name.get((module,str(token)));nodes.append((token,obj))
        local={obj.name:obj for _,obj in nodes if obj};numeric={};references={}
        for name,obj in local.items():
            identifiers=list(parts(obj.declaration,'objectIdentifier'))
            if not identifiers:continue
            bits=identifiers[-1][1];numbers=[];parent=None
            for item in bits:
                if isinstance(item,int):numbers.append(item)
                elif isinstance(item,tuple):numbers.append(int(item[1]))
                elif item in BASE:numbers.extend(BASE[item])
                else:parent=item;break
            if parent in local and (len(bits)>1 or obj.kind=='TRAP-TYPE'):references[name]=parent
            if parent is None:
                if obj.kind=='TRAP-TYPE':numbers.extend((0,int(obj.declaration[-1])))
                numeric[name]=tuple(numbers)
        # Resolve numeric OIDs in dependency order, also for forward references.
        pending={name for name,obj in local.items() if name not in numeric and list(parts(obj.declaration,'objectIdentifier'))}
        while pending:
            progressed=False
            for name in tuple(pending):
                obj=local[name];bits=list(parts(obj.declaration,'objectIdentifier'))[-1][1];numbers=[]
                for bit in bits:
                    if isinstance(bit,int):numbers.append(bit)
                    elif isinstance(bit,tuple):numbers.append(int(bit[1]))
                    elif bit in numeric:numbers.extend(numeric[bit])
                    elif bit in BASE:numbers.extend(BASE[bit])
                    else:break
                else:
                    if obj.kind=='TRAP-TYPE':numbers.extend((0,int(obj.declaration[-1])))
                    numeric[name]=tuple(numbers);pending.remove(name);progressed=True
            if not progressed:break
        for name,oid in numeric.items():
            parents=[other for other,number in numeric.items() if other!=name and len(number)<len(oid) and oid[:len(number)]==number]
            if parents:references[name]=max(parents,key=lambda other:len(numeric[other]))
        depths={}
        for name in local:
            chain=[];seen=set();current=name
            while current not in depths and current not in seen:
                seen.add(current);chain.append(current)
                if current not in references:break
                current=references[current]
            base=depths[current]+1 if current in depths else 0
            if current in seen and current in references and references[current] in seen:
                for item in chain:depths[item]=0
            else:
                for item in reversed(chain):depths[item]=base;base+=1
        for token,obj in nodes:result[token.start_pos]=Declaration(token.start_pos,token.end_pos,obj.kind if obj else '',depths.get(str(token),0))
        modules.append(name_token.start_pos)
    return result,sorted(modules)


def format_mib(text):
    """Change layout only. Every lexical token and every comment is retained."""
    if len(text.encode('utf-8'))>MAX_MIB:raise MibFormatError('MIB exceeds 4 MiB')
    parser=SmiStarParser()
    try:
        original=parser.parse(text);masked,tokens=_lexical_source(parser,text);declarations,module_starts=_declarations(parser,text,masked)
        for index,token in enumerate(tokens):
            if token.type=='PRESERVED' and str(token).startswith('MACRO') and index:
                name=tokens[index-1];declarations.setdefault(name.start_pos,Declaration(name.start_pos,token.end_pos,'MACRO'))
    except MibFormatError:raise
    except Exception as exc:raise MibFormatError(str(exc)) from exc
    starts=set(module_starts);module_ends=set();level=0
    for token in tokens:
        if str(token)=='BEGIN':level+=1
        elif str(token)=='END':
            level-=1
            if level==0:module_ends.add(token.start_pos)
    writer=Writer();active=None;mode='header';braces=[];parens=0;compact_oid=False;previous_end=0;seen_comments=[]
    for index,token in enumerate(tokens):
        value=str(token);following=str(tokens[index+1]) if index+1<len(tokens) else ''
        new=declarations.get(token.start_pos);base=(new or active).depth*4 if new or active else 0
        indent=(base if new else braces[-1][1]+4 if braces and braces[-1][0] else base+4 if active else 0)
        if mode=='imports':indent=8 if value=='FROM' else 4
        if token.start_pos in module_ends:indent=0
        gap=text[previous_end:token.start_pos];gap_lines=gap.splitlines()
        if writer.line and gap and not gap.startswith(('\n','\r')) and gap_lines and gap_lines[0].strip():
            comment=gap_lines.pop(0).lstrip(' \t');seen_comments.append(comment);writer.comment(comment,indent,inline=True)
        if new or token.start_pos in starts or token.start_pos in module_ends:writer.blank()
        for line in gap_lines:
            if line.strip():
                comment=line.lstrip(' \t');seen_comments.append(comment);writer.comment(comment,indent)
        if token.start_pos in starts:
            writer.flush();mode='header';active=None;braces=[];parens=0
        if new:
            writer.flush();active=new;mode='declaration';compact_oid=False;braces=[];parens=0;base=new.depth*4;indent=base
            # OBJECT IDENTIFIER has a two-token declaration header.
            new.header_end=tokens[min(index+(2 if new.kind=='OBJECT IDENTIFIER' else 1),len(tokens)-1)].end_pos
        in_header=active is not None and token.start_pos<active.header_end
        if in_header:indent=base
        if token.start_pos in module_ends:
            writer.flush();writer.atom(value,0);active=None;mode='header'
        elif mode in ('header','global'):
            if value in ('IMPORTS','EXPORTS'):
                writer.blank();writer.atom(value,0);writer.flush();mode='imports'
            else:
                writer.atom(value,0)
                if value=='BEGIN':writer.blank();mode='global'
        elif mode=='imports':
            if value=='FROM':writer.flush();writer.atom(value,8)
            elif value==';':writer.atom(value,writer.indent);writer.blank();mode='global'
            else:
                after_from=writer.last=='FROM';writer.atom(value,8 if after_from else 4)
                if value==',' or after_from and following!=';':writer.flush()
        elif value=='::=' and not braces and not parens:
            if active and active.kind!='TYPE':writer.flush()
            writer.atom(value,base if active and active.kind=='TYPE' else base+4);compact_oid=bool(active and active.kind!='TYPE')
        elif value in CLAUSES and not braces and not parens and not in_header and not (value=='OBJECT' and index and str(tokens[index-1]) in ('SYNTAX','WRITE-SYNTAX','::=')):
            writer.flush();writer.atom(value,base+4)
        elif value=='{':
            multiline=not compact_oid;writer.atom(value,indent);braces.append((multiline,writer.indent))
            if multiline:writer.flush()
        elif value=='}' and braces:
            multiline,opening=braces.pop()
            if multiline:writer.flush();writer.atom(value,opening)
            else:writer.atom(value,writer.indent)
        else:
            writer.atom(value,indent)
            if value=='(':parens+=1
            elif value==')':parens-=1
            if value==',' and braces and braces[-1][0] and not parens:writer.flush()
        previous_end=token.end_pos
    for line in text[previous_end:].splitlines():
        if line.strip():
            comment=line.lstrip(' \t');seen_comments.append(comment);writer.comment(comment,0,inline=not text[previous_end:].startswith(('\n','\r')))
    formatted=writer.result()
    if len(formatted.encode('utf-8'))>MAX_MIB:raise MibFormatError('Formatted MIB exceeds 4 MiB')
    try:
        output=parser.parse(formatted);_,new_tokens=_lexical_source(parser,formatted)
    except Exception as exc:raise MibFormatError('Unable to format the MIB without changing its syntax: '+str(exc)) from exc
    if original!=output or [str(token) for token in tokens]!=[str(token) for token in new_tokens]:raise MibFormatError('Formatting would change the MIB content.')
    comments=[];end=0
    for token in new_tokens:
        comments.extend(line.lstrip(' \t') for line in formatted[end:token.start_pos].splitlines() if line.strip());end=token.end_pos
    comments.extend(line.lstrip(' \t') for line in formatted[end:].splitlines() if line.strip())
    if comments!=seen_comments:raise MibFormatError('Formatting would change the MIB comments.')
    return formatted
