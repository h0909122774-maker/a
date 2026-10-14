import sys
s=open(sys.argv[1],encoding='utf-8').read()
d=0;ins=False;com=False;esc=False;line=1;bad=0;start=None
for c in s:
    if c=='\n': line+=1
    if com:
        if c=='\n': com=False
        continue
    if ins:
        if esc: esc=False
        elif c=='\\': esc=True
        elif c=='"': ins=False
        continue
    if c==';': com=True
    elif c=='"': ins=True
    elif c=='(':
        if d==0: start=line
        d+=1
    elif c==')':
        d-=1
        if d<0: print('extra ) at line',line); d=0; bad=1
    elif d==0 and not c.isspace():
        print('stray top-level text at line',line, repr(c)); bad=1
print('final depth',d,'OK' if d==0 and not bad else 'BAD')
