#!/usr/bin/env python3
"""Dump von MovieObject.bdmv (Navigation-Commands, lesbar)."""
import sys
from pathlib import Path

BRANCH = {(0,0):"NOP",(0,1):"GOTO",(0,2):"BREAK",
          (1,0):"JUMP_OBJECT",(1,1):"JUMP_TITLE",(1,2):"CALL_OBJECT",
          (1,3):"CALL_TITLE",(1,4):"RESUME",
          (2,0):"PLAY_PL",(2,1):"PLAY_PL_PI",(2,2):"PLAY_PL_PM",
          (2,3):"TERMINATE_PL",(2,4):"LINK_PI",(2,5):"LINK_MK"}
CMP = {1:"BC",2:"EQ",3:"NE",4:"GE",5:"GT",6:"LE",7:"LT"}
SETSET = {1:"MOVE",2:"SWAP",3:"ADD",4:"SUB",5:"MUL",6:"DIV",7:"MOD",8:"RND",
          9:"AND",10:"OR",11:"XOR",12:"BITSET",13:"BITCLR",14:"SHL",15:"SHR"}
SETSYS = {1:"SET_STREAM",2:"SET_NV_TIMER",3:"SET_BUTTON_PAGE",4:"ENABLE_BUTTON",
          5:"DISABLE_BUTTON",6:"SET_SEC_STREAM",7:"POPUP_OFF",8:"STILL_ON",
          9:"STILL_OFF",10:"SET_OUTPUT_MODE",11:"SET_STREAM_SS",16:"SETSYSTEM_0x10"}
JUMPS = {"JUMP_OBJECT","CALL_OBJECT","JUMP_TITLE","CALL_TITLE"}

def decode(c):
    w = int.from_bytes(c[0:4], "big")
    op_cnt, grp, sub = (w >> 29) & 7, (w >> 27) & 3, (w >> 24) & 7
    imm1, imm2 = (w >> 23) & 1, (w >> 22) & 1
    bopt, copt, sopt = (w >> 16) & 15, (w >> 8) & 15, w & 31
    if grp == 0:   name = BRANCH.get((sub, bopt), f"BRANCH?{sub}/{bopt}")
    elif grp == 1: name = "CMP_" + CMP.get(copt, f"?{copt}")
    else:          name = (SETSET if sub == 0 else SETSYS).get(sopt, f"SET?{sub}/{sopt}")
    def op(v, imm, reg):
        return f"#{v}" if imm else (f"GPR{(v>>8)&0xff}" if v & 0x80000000 else f"PSR{(v>>8)&0xff}")
    dst = op(int.from_bytes(c[4:8], "big"), imm1, "d")
    src = op(int.from_bytes(c[8:12], "big"), imm2, "s") if op_cnt > 1 else ""
    return name, dst, src

def main():
    d = Path(sys.argv[1]).read_bytes()
    assert d[0:4] == b"MOBJ", "keine MOBJ-Signatur"
    n = int.from_bytes(d[48:50], "big")
    print(f"{n} Objekte, data_len {int.from_bytes(d[40:44],'big')}, {len(d)} Bytes")
    o = 50
    for i in range(n):
        flags = int.from_bytes(d[o:o+2], "big")
        cnt = int.from_bytes(d[o+2:o+4], "big")
        print(f"\n=== Objekt {i}: {cnt} Kommandos, flags=0x{flags:04x} "
              f"(resume={(flags>>15)&1} menu={(flags>>14)&1} search={(flags>>13)&1})")
        off = o + 4
        for k in range(cnt):
            name, dst, src = decode(d[off:off+12])
            mark = " <<<" if name in JUMPS else ""
            print(f"  {k:3d}  {name:14s} {dst:12s} {src}{mark}")
            off += 12
        o = off

if __name__ == "__main__":
    main()