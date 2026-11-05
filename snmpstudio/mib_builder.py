# SPDX-License-Identifier: Apache-2.0
"""Small source patches for a visual MIB editor; unsupported declarations stay intact."""
from dataclasses import dataclass
import re
from pysmi.parser import SmiStarParser
from .mib import parse_source, parts, BASE

KINDS=('OBJECT IDENTIFIER','OBJECT-TYPE','NOTIFICATION-TYPE','OBJECT-IDENTITY','MODULE-IDENTITY')
CLAUSES={'SYNTAX','UNITS','MAX-ACCESS','ACCESS','STATUS','DESCRIPTION','REFERENCE','INDEX','AUGMENTS','DEFVAL',
         'OBJECTS','LAST-UPDATED','ORGANIZATION','CONTACT-INFO','REVISION','DISPLAY-HINT'}
SYNTAXES=('Integer32','Unsigned32','Gauge32','Counter32','Counter64','TimeTicks','INTEGER','OCTET STRING','OBJECT IDENTIFIER','IpAddress','BITS','DisplayString','TruthValue')
SMI_TYPES={'Integer32','Unsigned32','Gauge32','Counter32','Counter64','TimeTicks','IpAddress'}


def quoted(text):
    # SMI descriptions have no portable embedded-double-quote escape.
    if '"' in text:raise ValueError('Use typographic quotes (« ») inside SMI text.')
    return '"'+text+'"'


def symbol(text):
    if not re.fullmatch(r'[a-z][A-Za-z0-9-]*',text) or text.endswith('-') or '--' in text:
        raise ValueError('Expected an object name beginning with a lowercase letter.')
    return text


@dataclass
class Node:
    obj: object
    start: int
    end: int
    tokens: list


class BuilderDocument:
    def __init__(self,text,context=None):
        self.context=context or {}
        self.text=text;self.objects,self.imports,errors=parse_source(text)
        if errors:raise ValueError('\n'.join(d.message for d in errors))
        if len(self.imports)!=1:raise ValueError('The constructor needs one MIB module per file. Use the source editor for this file.')
        self.module=next(iter(self.imports));self.v1='OBJECT-TYPE' in self.imports[self.module].get('RFC-1212',[])
        self.tokens=list(SmiStarParser().parser.lex(text));self.nodes=[]
        cursor=next(i for i,t in enumerate(self.tokens) if str(t)=='BEGIN')+1
        if cursor<len(self.tokens) and str(self.tokens[cursor])=='IMPORTS':
            cursor=next(i+1 for i in range(cursor,len(self.tokens)) if str(self.tokens[i])==';')
        starts=[]
        for obj in self.objects:
            header=('OBJECT','IDENTIFIER') if obj.kind=='OBJECT IDENTIFIER' else ('::=',) if obj.kind=='TYPE' else (obj.kind,)
            at=next((i for i in range(cursor,len(self.tokens)-len(header)) if str(self.tokens[i])==obj.name and tuple(str(t) for t in self.tokens[i+1:i+1+len(header)])==header),None)
            if at is None:raise ValueError('Cannot locate declaration: '+obj.name)
            starts.append((obj,at));cursor=at+1
        self.end_token=next(t for t in reversed(self.tokens) if str(t)=='END')
        for number,(obj,at) in enumerate(starts):
            limit=starts[number+1][1] if number+1<len(starts) else self.tokens.index(self.end_token)
            tokens=self.tokens[at:limit]
            if obj.kind in KINDS:
                assignment=next((i for i,t in reversed(list(enumerate(tokens))) if str(t)=='::='),None)
                if assignment is None:raise ValueError('Missing OID assignment: '+obj.name)
                level=0;end=None
                for token in tokens[assignment+1:]:
                    if str(token)=='{':level+=1
                    elif str(token)=='}':
                        level-=1
                        if level==0:end=token.end_pos;break
                if end is None:raise ValueError('Incomplete OID assignment: '+obj.name)
                tokens=[t for t in tokens if t.end_pos<=end]
            else:end=tokens[-1].end_pos
            self.nodes.append(Node(obj,tokens[0].start_pos,end,tokens))

    def node(self,name):return next(node for node in self.nodes if node.obj.name==name)

    def fields(self,node):
        result={'name':node.obj.name,'kind':node.obj.kind,'description':node.obj.description,'unit':node.obj.units,
                'syntax':node.obj.syntax or 'Integer32','access':node.obj.access or 'read-only','status':'current',
                'parent':'','number':'','constraints':'','objects':'','organization':'','contact':'','updated':''}
        spans=self.clause_spans(node)
        for key,clause in [('status','STATUS'),('organization','ORGANIZATION'),('contact','CONTACT-INFO'),('updated','LAST-UPDATED')]:
            if clause in spans:
                a,b=spans[clause];result[key]=self.text[a:b].strip('"')
        if 'SYNTAX' in spans:
            a,b=spans['SYNTAX'];raw=self.text[a:b]
            for syntax in sorted(SYNTAXES,key=len,reverse=True):
                if raw==syntax or raw.startswith(syntax+' ') or raw.startswith(syntax+'\n'):
                    result['syntax']=syntax;result['constraints']=raw[len(syntax):].strip();break
            else:result['syntax']=raw;result['constraints']=''
        if 'OBJECTS' in spans:
            a,b=spans['OBJECTS'];result['objects']=self.text[a:b].strip().strip('{}').strip()
        oid=list(parts(node.obj.declaration,'objectIdentifier'))
        if oid:
            bits=oid[-1][1]
            result['number']=str(bits[-1][1] if isinstance(bits[-1],tuple) else bits[-1])
            result['parent']=' '.join(f'{x[0]}({x[1]})' if isinstance(x,tuple) else str(x) for x in bits[:-1])
        return result

    def clause_spans(self,node):
        starts=[];depth=0
        for token in node.tokens[1:]:
            value=str(token)
            if depth==0 and (value in CLAUSES or value=='::='):starts.append(token)
            if value in ('{','('):depth+=1
            if value in ('}',')'):depth-=1
        spans={}
        for index,token in enumerate(starts):
            if str(token)=='::=':continue
            limit=starts[index+1].start_pos if index+1<len(starts) else node.end
            values=[t for t in node.tokens if token.end_pos<=t.start_pos<limit]
            if values:spans.setdefault(str(token),(values[0].start_pos,values[-1].end_pos))
        return spans

    def values(self,fields):
        kind=fields['kind'];name=symbol(fields['name'].strip());parent=fields['parent'].strip()
        if not re.fullmatch(r'(?:[a-z][A-Za-z0-9-]*(?:\(\d+\))?|\d+)(?:\s+(?:[a-z][A-Za-z0-9-]*(?:\(\d+\))?|\d+))*',parent):raise ValueError('Select an OID parent or enter its numeric arcs separated by spaces.')
        number=int(fields['number'])
        if not 0<=number<=4294967295:raise ValueError('OID number must be between 0 and 4294967295.')
        values={};required=[]
        if kind=='MODULE-IDENTITY':
            if not re.fullmatch(r'\d{12}Z',fields['updated']):raise ValueError('LAST-UPDATED: YYYYMMDDhhmmZ')
            for key,clause in [('updated','LAST-UPDATED'),('organization','ORGANIZATION'),('contact','CONTACT-INFO')]:values[clause]=quoted(fields[key])
        if kind=='OBJECT-TYPE':
            syntax=fields['syntax'].strip();constraints=fields['constraints'].strip()
            values['SYNTAX']=syntax+(' '+constraints if constraints else '')
            values['UNITS']=quoted(fields['unit']) if fields['unit'] else None
            values['ACCESS' if self.v1 else 'MAX-ACCESS']=fields['access'];required.append('OBJECT-TYPE')
            if syntax in SMI_TYPES:required.append(syntax)
            if syntax in ('DisplayString','TruthValue'):required.append(syntax)
        if kind=='NOTIFICATION-TYPE':
            names=[symbol(x.strip()) for x in fields['objects'].split(',') if x.strip()]
            if any(x not in [obj.name for obj in self.objects if obj.kind=='OBJECT-TYPE'] for x in names):raise ValueError('Notification objects must be OBJECT-TYPE parameters defined in this module.')
            values['OBJECTS']='{ '+', '.join(names)+' }' if names else None
        if kind in ('OBJECT-TYPE','NOTIFICATION-TYPE','OBJECT-IDENTITY'):values['STATUS']=fields['status']
        if kind!='OBJECT IDENTIFIER':values['DESCRIPTION']=quoted(fields['description'])
        if kind!='OBJECT IDENTIFIER':required.append(kind)
        if parent in BASE and not any(obj.name==parent for obj in self.objects):required.append(parent)
        return name,parent,number,values,required

    def change(self,fields,name=None):
        new_name,parent,number,values,required=self.values(fields)
        if name is None:
            if any(obj.name==new_name for obj in self.objects):raise ValueError('Duplicate object name: '+new_name)
            block=new_name+' '+fields['kind']+'\n'
            block+=''.join('    '+key+' '+value+'\n' for key,value in values.items() if value is not None)
            block+='    ::= { '+parent+' '+str(number)+' }\n\n'
            result=self.text[:self.end_token.start_pos]+block+self.text[self.end_token.start_pos:]
        else:
            node=self.node(name)
            if fields['kind']!=node.obj.kind:raise ValueError('An existing object keeps its declaration kind.')
            patches=[]
            if new_name!=name:
                if any(obj.name==new_name for obj in self.objects):raise ValueError('Duplicate object name: '+new_name)
                for source in self.context.values():
                    _,imports,_=parse_source(source)
                    if any(name in modules.get(self.module,[]) for modules in imports.values()):raise ValueError('This name is imported by another open MIB. Update its imports in source mode before renaming.')
                first=self.nodes[0].start
                patches.extend((token.start_pos,token.end_pos,new_name) for index,token in enumerate(self.tokens) if token.start_pos>=first and str(token)==name and (index+1==len(self.tokens) or str(self.tokens[index+1])!='('))
            spans=self.clause_spans(node)
            for key,value in values.items():
                target='ACCESS' if key=='MAX-ACCESS' and 'ACCESS' in spans else key
                if target in spans:
                    a,b=spans[target]
                    if value is not None:
                        if self.text[a:b]!=value:patches.append((a,b,value))
                    else:
                        token=next(t for t in node.tokens if str(t)==target)
                        patches.append((token.start_pos,b,''))
                elif value is not None:
                    # Insert before the final assignment, preserving existing optional clauses.
                    following=list(values)[list(values).index(key)+1:]+['REFERENCE','INDEX','AUGMENTS','DEFVAL','::=']
                    token=next(t for t in node.tokens if str(t) in following)
                    patches.append((token.start_pos,token.start_pos,key+' '+value+'\n    '))
            assignment=next(t for t in reversed(node.tokens) if str(t)=='::=')
            begin=next(t for t in node.tokens if t.start_pos>assignment.end_pos and str(t)=='{')
            replacement='{ '+parent+' '+str(number)+' }'
            original=self.fields(node)
            if original['parent']!=parent or original['number']!=str(number):patches.append((begin.start_pos,node.end,replacement))
            result=self.patch(self.text,patches)
        result=self.add_imports(result,required)
        _,_,errors=parse_source(result)
        if errors:raise ValueError('\n'.join(d.message for d in errors))
        # Catch duplicate OIDs and unresolved parent names using this document's definitions.
        from .mib import MibIndex
        after=MibIndex().rebuild({**self.context,'source':result})
        changed=next(obj for obj in after.objects if obj.name==new_name and obj.module==self.module)
        if not changed.oid:raise ValueError('The parent OID could not be resolved. Load its defining MIB or use numeric arcs.')
        from .models import oid_tuple
        oid_tuple(changed.oid)
        duplicates=[obj.name for obj in after.objects if obj.module==self.module and obj.name!=new_name and obj.oid==changed.oid]
        if duplicates:raise ValueError('OID already used by '+', '.join(duplicates))
        return result

    @staticmethod
    def patch(text,patches):
        for a,b,value in sorted(patches,key=lambda item:item[0],reverse=True):text=text[:a]+value+text[b:]
        return text

    def add_imports(self,text,required):
        _,imports,errors=parse_source(text)
        if errors:raise ValueError('\n'.join(d.message for d in errors))
        tokens=list(SmiStarParser().parser.lex(text))
        present=imports[self.module];additions={}
        for name in required:
            module='SNMPv2-TC' if name in ('DisplayString','TruthValue') else 'SNMPv2-SMI'
            if not any(name in names for names in present.values()):additions.setdefault(module,[]).append(name)
        if not additions:return text
        patches=[]
        if not present:
            at=next(t.end_pos for t in tokens if str(t)=='BEGIN')
            block='\n\nIMPORTS\n'+''.join('    '+', '.join(dict.fromkeys(names))+' FROM '+module+'\n' for module,names in additions.items())+';\n'
            return self.patch(text,[(at,at,block)])
        for module,names in additions.items():
            if module in present:
                start=next(i for i,t in enumerate(tokens) if str(t)=='IMPORTS')
                at=next(tokens[i].start_pos for i in range(start,len(tokens)-1) if str(tokens[i])=='FROM' and str(tokens[i+1])==module)
                patches.append((at,at,', '+', '.join(dict.fromkeys(names))+'\n        '))
            else:
                block='    '+', '.join(dict.fromkeys(names))+' FROM '+module+'\n'
                if present:
                    start=next(i for i,t in enumerate(tokens) if str(t)=='IMPORTS')
                    at=next(t.start_pos for t in tokens[start:] if str(t)==';');patches.append((at,at,'\n'+block))
                else:
                    at=next(t.end_pos for t in tokens if str(t)=='BEGIN');patches.append((at,at,'\n\nIMPORTS\n'+block+';'))
        return self.patch(text,patches)

    def delete(self,name):
        node=self.node(name)
        if node.obj.kind not in KINDS:raise ValueError('Edit this declaration in source mode.')
        for source in self.context.values():
            _,imports,_=parse_source(source)
            if any(name in modules.get(self.module,[]) for modules in imports.values()):raise ValueError('This object is imported by another open MIB.')
        refs=[t for t in self.tokens if str(t)==name and not node.start<=t.start_pos<node.end]
        if refs:raise ValueError('This object is referenced elsewhere in the MIB. Remove dependent objects first.')
        result=self.text[:node.start]+self.text[node.end:]
        if parse_source(result)[2]:raise ValueError('Deletion would make the MIB invalid.')
        return result
