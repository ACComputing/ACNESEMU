#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACNESEMU 0.2 — A.C Holdings / Team Flames
Commercial + homebrew NES emulator.
Mapper 0 (NROM), 1 (MMC1), 2 (UxROM), 3 (CNROM).
Full 6502 CPU + scanline PPU + sprite rendering + scrolling.
Pure Python 3. Requires: pip install pillow
Copyright (C) 1999-2026 A.C Holdings / Team Flames
"""

import os, threading, time
import tkinter as tk
from tkinter import filedialog, Menu, messagebox
from PIL import Image, ImageTk

# ── NTSC Palette ──────────────────────────────────────────────────────────────
NES_PAL = [
    0x626262,0x001FB2,0x2404C8,0x5200B2,0x730076,0x800024,0x730B00,0x522800,
    0x244400,0x005700,0x005C00,0x005324,0x003C76,0x000000,0x000000,0x000000,
    0xABABAB,0x0D57FF,0x4B30FF,0x8A13FF,0xBC08D6,0xD21269,0xC72E00,0x9D5400,
    0x607B00,0x209800,0x00A300,0x009942,0x007DB4,0x000000,0x000000,0x000000,
    0xFFFFFF,0x53AEFF,0x9085FF,0xD365FF,0xFF57FF,0xFF5DCF,0xFF7757,0xFA9E00,
    0xBDC700,0x7AE700,0x43F611,0x26EF7E,0x2CD5F6,0x4E4E4E,0x000000,0x000000,
    0xFFFFFF,0xB6DFFF,0xC3CFFF,0xD9C3FF,0xE8BCFF,0xFFBDF4,0xFFC6C3,0xFFD59A,
    0xE9E681,0xCEF481,0xB6FB9A,0xA9FAC3,0xA9F0F6,0xB8B8B8,0x000000,0x000000,
]
# Precompute flat RGB bytes for fast framebuffer conversion
_PAL_RGB = bytearray()
for _c in NES_PAL:
    _PAL_RGB += bytes([(_c >> 16) & 0xFF, (_c >> 8) & 0xFF, _c & 0xFF])

# ── Mappers ───────────────────────────────────────────────────────────────────
MIR_H, MIR_V, MIR_SL, MIR_SH, MIR_4 = 0, 1, 2, 3, 4

class Mapper:
    def __init__(self, prg, chr_data, mirror):
        self.prg    = bytes(prg)
        self.chr    = bytearray(chr_data) if chr_data else bytearray(0x2000)
        self.mirror = mirror
        self.chr_ram = len(chr_data) == 0

    def read_prg(self, addr): return 0
    def write_prg(self, addr, val): pass
    def read_chr(self, addr): return self.chr[addr & (len(self.chr) - 1)]
    def write_chr(self, addr, val):
        if self.chr_ram: self.chr[addr & 0x1FFF] = val

    def nt_mirror(self, addr):
        a = addr & 0x0FFF
        if   self.mirror == MIR_H:  return (a & 0x3FF) | (0x400 if a >= 0x800 else 0)
        elif self.mirror == MIR_V:  return a & 0x7FF
        elif self.mirror == MIR_SL: return a & 0x3FF
        elif self.mirror == MIR_SH: return 0x400 | (a & 0x3FF)
        return a & 0x7FF


class Mapper0(Mapper):
    def __init__(self, prg, chr_data, mirror):
        super().__init__(prg, chr_data, mirror)
        self._mask = len(prg) - 1

    def read_prg(self, addr):
        return self.prg[(addr - 0x8000) & self._mask] if addr >= 0x8000 else 0


class Mapper1(Mapper):
    """MMC1 — Zelda, Mega Man 2, Metroid …"""
    def __init__(self, prg, chr_data, mirror):
        super().__init__(prg, chr_data, mirror)
        self._shift = 0x10; self._ctrl = 0x0C
        self._chr0 = self._chr1 = self._prg_bank = 0
        self._prg_mode = 3; self._chr_mode = 0
        self._prg_n = len(prg) // 0x4000
        self._chr_n = max(1, len(chr_data) // 0x1000) if chr_data else 0

    def _apply_ctrl(self):
        m = self._ctrl & 3
        self.mirror = [MIR_SL, MIR_SH, MIR_V, MIR_H][m]
        self._prg_mode = (self._ctrl >> 2) & 3
        self._chr_mode = (self._ctrl >> 4) & 1

    def write_prg(self, addr, val):
        if addr < 0x8000: return
        if val & 0x80:
            self._shift = 0x10; self._ctrl |= 0x0C; self._apply_ctrl(); return
        done = self._shift & 1
        self._shift = (self._shift >> 1) | ((val & 1) << 4)
        if done:
            reg = (addr >> 13) & 3
            if   reg == 0: self._ctrl = self._shift; self._apply_ctrl()
            elif reg == 1: self._chr0 = self._shift
            elif reg == 2: self._chr1 = self._shift
            elif reg == 3: self._prg_bank = self._shift & 0x0F
            self._shift = 0x10

    def read_prg(self, addr):
        if addr < 0x8000: return 0
        L = len(self.prg)
        if self._prg_mode in (0, 1):
            return self.prg[((self._prg_bank & 0xFE) * 0x4000 + (addr - 0x8000)) % L]
        elif self._prg_mode == 2:
            if addr < 0xC000: return self.prg[(addr - 0x8000) % L]
            return self.prg[(self._prg_bank * 0x4000 + (addr - 0xC000)) % L]
        else:
            if addr >= 0xC000: return self.prg[((self._prg_n - 1) * 0x4000 + (addr - 0xC000)) % L]
            return self.prg[(self._prg_bank * 0x4000 + (addr - 0x8000)) % L]

    def read_chr(self, addr):
        if self.chr_ram: return self.chr[addr & 0x1FFF]
        L = len(self.chr)
        if self._chr_mode == 0:
            return self.chr[((self._chr0 & 0xFE) * 0x1000 + addr) % L]
        return self.chr[((self._chr0 if addr < 0x1000 else self._chr1) * 0x1000 + (addr & 0xFFF)) % L]

    def write_chr(self, addr, val):
        if self.chr_ram: self.chr[addr & 0x1FFF] = val


class Mapper2(Mapper):
    """UxROM — Mega Man, Castlevania, DuckTales …"""
    def __init__(self, prg, chr_data, mirror):
        super().__init__(prg, chr_data, mirror)
        self._bank = 0
        self._last = (len(prg) // 0x4000) - 1

    def read_prg(self, addr):
        if addr < 0x8000: return 0
        if addr < 0xC000: return self.prg[(self._bank * 0x4000 + (addr - 0x8000)) % len(self.prg)]
        return self.prg[(self._last * 0x4000 + (addr - 0xC000)) % len(self.prg)]

    def write_prg(self, addr, val):
        if addr >= 0x8000: self._bank = val & 0x0F


class Mapper3(Mapper):
    """CNROM — Gradius, Arkanoid …"""
    def __init__(self, prg, chr_data, mirror):
        super().__init__(prg, chr_data, mirror)
        self._chr_bank = 0
        self._prg_mask = len(prg) - 1

    def read_prg(self, addr):
        return self.prg[(addr - 0x8000) & self._prg_mask] if addr >= 0x8000 else 0

    def write_prg(self, addr, val):
        if addr >= 0x8000: self._chr_bank = val & 3

    def read_chr(self, addr):
        return self.chr[(self._chr_bank * 0x2000 + addr) % len(self.chr)]


def make_mapper(mid, prg, chr_data, mirror):
    cls = {0: Mapper0, 1: Mapper1, 2: Mapper2, 3: Mapper3}.get(mid)
    if cls is None:
        raise ValueError(f"Mapper {mid} not supported (need 0/1/2/3)")
    return cls(prg, chr_data, mirror)


# ── PPU ───────────────────────────────────────────────────────────────────────
class PPU:
    def __init__(self):
        self.vram    = bytearray(0x800)
        self.palette = bytearray(32)
        self.oam     = bytearray(256)
        self.fb      = bytearray(256 * 240)   # NES palette indices (0-63)
        self.ctrl = self.mask = self.status = 0
        self.oam_addr = 0
        self.v = self.t = 0   # Loopy registers
        self.x = 0            # fine X scroll
        self.w = 0            # write toggle
        self.data_buf = 0
        self.mapper   = None
        self.nmi_out  = False

    def reset(self):
        self.ctrl = self.mask = self.status = 0
        self.oam_addr = self.v = self.t = self.x = self.w = self.data_buf = 0
        self.nmi_out = False

    def connect(self, mapper): self.mapper = mapper

    # ── PPU bus ──
    def _read(self, addr):
        addr &= 0x3FFF
        if   addr < 0x2000: return self.mapper.read_chr(addr)
        elif addr < 0x3F00: return self.vram[self.mapper.nt_mirror(addr)]
        else:
            i = addr & 0x1F
            if i in (0x10,0x14,0x18,0x1C): i &= 0x0F
            return self.palette[i] & 0x3F

    def _write(self, addr, val):
        addr &= 0x3FFF
        if   addr < 0x2000: self.mapper.write_chr(addr, val)
        elif addr < 0x3F00: self.vram[self.mapper.nt_mirror(addr)] = val
        else:
            i = addr & 0x1F
            if i in (0x10,0x14,0x18,0x1C): i &= 0x0F
            self.palette[i] = val & 0x3F

    # ── CPU-side register I/O ──
    def read_reg(self, reg):
        if reg == 2:   # STATUS
            v = (self.status & 0xE0) | (self.data_buf & 0x1F)
            self.status &= ~0x80; self.w = 0; return v
        if reg == 4:   return self.oam[self.oam_addr]
        if reg == 7:   # PPUDATA
            addr = self.v
            if addr < 0x3F00:
                val = self.data_buf; self.data_buf = self._read(addr)
            else:
                self.data_buf = self._read(addr - 0x1000); val = self._read(addr)
            self.v = (self.v + (32 if self.ctrl & 4 else 1)) & 0x7FFF
            return val
        return 0

    def write_reg(self, reg, val):
        if reg == 0:   # PPUCTRL
            was = self.ctrl & 0x80
            self.ctrl = val
            self.t = (self.t & 0xF3FF) | ((val & 3) << 10)
            if not was and (self.ctrl & 0x80) and (self.status & 0x80):
                self.nmi_out = True
        elif reg == 1: self.mask = val
        elif reg == 3: self.oam_addr = val
        elif reg == 4:
            self.oam[self.oam_addr] = val; self.oam_addr = (self.oam_addr + 1) & 0xFF
        elif reg == 5: # PPUSCROLL
            if self.w == 0:
                self.t = (self.t & 0xFFE0) | (val >> 3); self.x = val & 7; self.w = 1
            else:
                self.t = (self.t & 0x8C1F) | ((val & 0xF8) << 2) | ((val & 7) << 12); self.w = 0
        elif reg == 6: # PPUADDR
            if self.w == 0:
                self.t = (self.t & 0x00FF) | ((val & 0x3F) << 8); self.w = 1
            else:
                self.t = (self.t & 0xFF00) | val; self.v = self.t; self.w = 0
        elif reg == 7:
            self._write(self.v, val)
            self.v = (self.v + (32 if self.ctrl & 4 else 1)) & 0x7FFF

    def oam_dma(self, page):
        for i in range(256):
            self.oam[(self.oam_addr + i) & 0xFF] = page[i]

    # ── Rendering ──
    def _render_scanline(self, sl):
        show_bg  = bool(self.mask & 0x08)
        show_spr = bool(self.mask & 0x10)
        base     = sl * 256
        bg_c     = [0] * 256
        bg_p     = [0] * 256

        if show_bg:
            fine_y   = (self.v >> 12) & 7
            coarse_y = (self.v >> 5) & 31
            nt_y     = (self.v >> 11) & 1
            start_cx = self.v & 31
            start_nx = (self.v >> 10) & 1
            pt_bg    = 0x1000 if (self.ctrl & 0x10) else 0

            x = 0
            while x < 256:
                tot  = x + self.x
                tidx = tot >> 3
                fpx  = tot & 7
                cx   = (start_cx + tidx) & 31
                nx   = (start_nx ^ ((start_cx + tidx) >> 5)) & 1

                nt   = 0x2000 | (nt_y << 11) | (nx << 10) | (coarse_y << 5) | cx
                tile = self._read(nt)
                at   = 0x2000 | (nt_y << 11) | (nx << 10) | 0x3C0 | ((coarse_y >> 2) << 3) | (cx >> 2)
                atb  = self._read(at)
                atp  = (atb >> (((coarse_y & 2) << 1) | (cx & 2))) & 3

                pa   = pt_bg + tile * 16 + fine_y
                lo   = self._read(pa); hi = self._read(pa + 8)

                run = min(8 - fpx, 256 - x)
                for j in range(run):
                    b = 7 - (fpx + j)
                    bg_c[x + j] = (((hi >> b) & 1) << 1) | ((lo >> b) & 1)
                    bg_p[x + j] = atp
                x += run

        spr_c  = [0] * 256
        spr_p  = [0] * 256
        spr_bh = [False] * 256   # behind background
        spr_z  = [False] * 256   # sprite 0

        if show_spr:
            spr_h  = 16 if (self.ctrl & 0x20) else 8
            pt_spr = 0x1000 if (self.ctrl & 0x08) else 0
            found  = 0
            for i in range(64):
                sy   = self.oam[i * 4]
                tile = self.oam[i * 4 + 1]
                attr = self.oam[i * 4 + 2]
                sx   = self.oam[i * 4 + 3]
                row  = sl - sy
                if not (0 <= row < spr_h): continue
                found += 1
                if found > 8: self.status |= 0x20; break

                flipv = bool(attr & 0x80); fliph = bool(attr & 0x40)
                bh    = bool(attr & 0x20); pal   = attr & 3

                if spr_h == 16:
                    pt = 0x1000 if (tile & 1) else 0; tile &= 0xFE
                    if flipv: row = 15 - row
                    if row >= 8: tile += 1; row -= 8
                    pa = pt + tile * 16 + row
                else:
                    if flipv: row = 7 - row
                    pa = pt_spr + tile * 16 + row

                lo = self._read(pa); hi = self._read(pa + 8)
                for col in range(8):
                    xi = sx + col
                    if xi >= 256 or spr_c[xi]: continue
                    b  = col if fliph else 7 - col
                    cc = (((hi >> b) & 1) << 1) | ((lo >> b) & 1)
                    if cc == 0: continue
                    spr_c[xi] = cc; spr_p[xi] = pal + 4; spr_bh[xi] = bh
                    if i == 0: spr_z[xi] = True

        for x in range(256):
            bc = bg_c[x]; sc = spr_c[x]
            if spr_z[x] and bc and sc and show_bg and show_spr and x < 255:
                self.status |= 0x40
            if sc and (not spr_bh[x] or not bc):
                pi = (spr_p[x] << 2) | sc
            elif bc:
                pi = (bg_p[x] << 2) | bc
            else:
                pi = 0
            self.fb[base + x] = self.palette[pi & 0x1F] & 0x3F

    def _inc_v_y(self):
        if not (self.mask & 0x18): return
        if (self.v & 0x7000) != 0x7000:
            self.v += 0x1000
        else:
            self.v &= ~0x7000
            y = (self.v >> 5) & 0x1F
            if   y == 29: y = 0; self.v ^= 0x0800
            elif y == 31: y = 0
            else:         y += 1
            self.v = (self.v & ~0x03E0) | (y << 5)

    def tick_frame(self):
        """Render one full frame. Returns True if NMI should fire."""
        self.nmi_out = False
        self.status &= ~0x60   # clear sprite-0-hit, overflow

        # Pre-render: copy vertical scroll from t to v
        vert = 0x7BE0
        self.v = (self.v & ~vert) | (self.t & vert)

        horiz = 0x041F
        for sl in range(240):
            # Each visible scanline: reload horizontal scroll from t
            self.v = (self.v & ~horiz) | (self.t & horiz)
            self._render_scanline(sl)
            self._inc_v_y()

        # VBlank
        self.status |= 0x80
        if self.ctrl & 0x80:
            self.nmi_out = True
        return self.nmi_out


# ── CPU ───────────────────────────────────────────────────────────────────────
class CPU:
    C=0x01; Z=0x02; I=0x04; D=0x08; B=0x10; U=0x20; V=0x40; N=0x80

    def __init__(self, bus):
        self.bus = bus
        self.A = self.X = self.Y = 0
        self.SP = 0xFD; self.P = 0x24; self.PC = 0
        self.cycles = 0
        self.stall  = 0
        self.nmi_pending = self.irq_pending = False

    def reset(self):
        self.SP = 0xFD; self.P = 0x24
        self.PC = self._r16(0xFFFC)
        self.cycles = 7; self.nmi_pending = self.irq_pending = False

    def _r(self, a):  return self.bus.read(a & 0xFFFF)
    def _w(self, a, v): self.bus.write(a & 0xFFFF, v & 0xFF)

    def _r16(self, a):
        return self._r(a) | (self._r(a + 1) << 8)

    def _r16b(self, a):   # indirect JMP page-cross bug
        return self._r(a) | (self._r((a & 0xFF00) | ((a + 1) & 0xFF)) << 8)

    def _push(self, v): self._w(0x100 | self.SP, v); self.SP = (self.SP - 1) & 0xFF
    def _pop(self):     self.SP = (self.SP + 1) & 0xFF; return self._r(0x100 | self.SP)

    def _gf(self, f): return bool(self.P & f)
    def _sf(self, f, v):
        if v: self.P |= f
        else: self.P &= ~f

    def _nz(self, v):
        self._sf(self.Z, v == 0); self._sf(self.N, v & 0x80); return v

    def trigger_nmi(self): self.nmi_pending = True
    def trigger_irq(self): self.irq_pending = True

    # ── Addressing modes ──────────────────────────────────────────────────────
    def _imm(self):
        v = self._r(self.PC); self.PC = (self.PC + 1) & 0xFFFF; return v, 0

    def _zp(self):
        a = self._r(self.PC); self.PC = (self.PC + 1) & 0xFFFF; return a, 0

    def _zpx(self):
        a = (self._r(self.PC) + self.X) & 0xFF; self.PC = (self.PC + 1) & 0xFFFF; return a, 0

    def _zpy(self):
        a = (self._r(self.PC) + self.Y) & 0xFF; self.PC = (self.PC + 1) & 0xFFFF; return a, 0

    def _abs(self):
        a = self._r16(self.PC); self.PC = (self.PC + 2) & 0xFFFF; return a, 0

    def _abx(self):
        b = self._r16(self.PC); self.PC = (self.PC + 2) & 0xFFFF
        a = (b + self.X) & 0xFFFF
        return a, (1 if (b & 0xFF00) != (a & 0xFF00) else 0)

    def _aby(self):
        b = self._r16(self.PC); self.PC = (self.PC + 2) & 0xFFFF
        a = (b + self.Y) & 0xFFFF
        return a, (1 if (b & 0xFF00) != (a & 0xFF00) else 0)

    def _inx(self):
        z = (self._r(self.PC) + self.X) & 0xFF; self.PC = (self.PC + 1) & 0xFFFF
        return self._r(z) | (self._r((z + 1) & 0xFF) << 8), 0

    def _iny(self):
        z = self._r(self.PC); self.PC = (self.PC + 1) & 0xFFFF
        b = self._r(z) | (self._r((z + 1) & 0xFF) << 8)
        a = (b + self.Y) & 0xFFFF
        return a, (1 if (b & 0xFF00) != (a & 0xFF00) else 0)

    def _branch(self, cond):
        off = self._r(self.PC); self.PC = (self.PC + 1) & 0xFFFF
        if off >= 0x80: off -= 0x100
        cyc = 2
        if cond:
            old = self.PC; self.PC = (self.PC + off) & 0xFFFF
            cyc += 1 + (1 if (old & 0xFF00) != (self.PC & 0xFF00) else 0)
        return cyc

    # ── ALU helpers ───────────────────────────────────────────────────────────
    def _adc(self, v):
        c = 1 if self._gf(self.C) else 0
        r = self.A + v + c
        self._sf(self.V, (~(self.A ^ v) & (self.A ^ r)) & 0x80)
        self._sf(self.C, r > 0xFF)
        self.A = r & 0xFF; self._nz(self.A)

    def _sbc(self, v): self._adc(v ^ 0xFF)

    def _and(self, v): self.A &= v; self._nz(self.A)
    def _ora(self, v): self.A |= v; self._nz(self.A)
    def _eor(self, v): self.A ^= v; self._nz(self.A)

    def _cmp(self, reg, v):
        r = reg - v
        self._sf(self.C, reg >= v); self._nz(r & 0xFF)

    def _asl(self, v):
        self._sf(self.C, v & 0x80); r = (v << 1) & 0xFF; self._nz(r); return r

    def _lsr(self, v):
        self._sf(self.C, v & 1); r = v >> 1; self._nz(r); return r

    def _rol(self, v):
        c = 1 if self._gf(self.C) else 0
        self._sf(self.C, v & 0x80); r = ((v << 1) | c) & 0xFF; self._nz(r); return r

    def _ror(self, v):
        c = 0x80 if self._gf(self.C) else 0
        self._sf(self.C, v & 1); r = (v >> 1) | c; self._nz(r); return r

    def _bit(self, v):
        self._sf(self.Z, (self.A & v) == 0)
        self._sf(self.V, v & 0x40); self._sf(self.N, v & 0x80)

    # ── Opcode dispatch ───────────────────────────────────────────────────────
    def _exec(self, op):
        # LDA
        if op==0xA9: a,p=self._imm(); self.A=self._nz(a); return 2
        if op==0xA5: a,p=self._zp();  self.A=self._nz(self._r(a)); return 3
        if op==0xB5: a,p=self._zpx(); self.A=self._nz(self._r(a)); return 4
        if op==0xAD: a,p=self._abs(); self.A=self._nz(self._r(a)); return 4
        if op==0xBD: a,p=self._abx(); self.A=self._nz(self._r(a)); return 4+p
        if op==0xB9: a,p=self._aby(); self.A=self._nz(self._r(a)); return 4+p
        if op==0xA1: a,p=self._inx(); self.A=self._nz(self._r(a)); return 6
        if op==0xB1: a,p=self._iny(); self.A=self._nz(self._r(a)); return 5+p
        # LDX
        if op==0xA2: a,p=self._imm(); self.X=self._nz(a); return 2
        if op==0xA6: a,p=self._zp();  self.X=self._nz(self._r(a)); return 3
        if op==0xB6: a,p=self._zpy(); self.X=self._nz(self._r(a)); return 4
        if op==0xAE: a,p=self._abs(); self.X=self._nz(self._r(a)); return 4
        if op==0xBE: a,p=self._aby(); self.X=self._nz(self._r(a)); return 4+p
        # LDY
        if op==0xA0: a,p=self._imm(); self.Y=self._nz(a); return 2
        if op==0xA4: a,p=self._zp();  self.Y=self._nz(self._r(a)); return 3
        if op==0xB4: a,p=self._zpx(); self.Y=self._nz(self._r(a)); return 4
        if op==0xAC: a,p=self._abs(); self.Y=self._nz(self._r(a)); return 4
        if op==0xBC: a,p=self._abx(); self.Y=self._nz(self._r(a)); return 4+p
        # STA
        if op==0x85: a,p=self._zp();  self._w(a,self.A); return 3
        if op==0x95: a,p=self._zpx(); self._w(a,self.A); return 4
        if op==0x8D: a,p=self._abs(); self._w(a,self.A); return 4
        if op==0x9D: a,p=self._abx(); self._w(a,self.A); return 5
        if op==0x99: a,p=self._aby(); self._w(a,self.A); return 5
        if op==0x81: a,p=self._inx(); self._w(a,self.A); return 6
        if op==0x91: a,p=self._iny(); self._w(a,self.A); return 6
        # STX
        if op==0x86: a,p=self._zp();  self._w(a,self.X); return 3
        if op==0x96: a,p=self._zpy(); self._w(a,self.X); return 4
        if op==0x8E: a,p=self._abs(); self._w(a,self.X); return 4
        # STY
        if op==0x84: a,p=self._zp();  self._w(a,self.Y); return 3
        if op==0x94: a,p=self._zpx(); self._w(a,self.Y); return 4
        if op==0x8C: a,p=self._abs(); self._w(a,self.Y); return 4
        # Transfers
        if op==0xAA: self.X=self._nz(self.A); return 2
        if op==0xA8: self.Y=self._nz(self.A); return 2
        if op==0xBA: self.X=self._nz(self.SP); return 2
        if op==0x8A: self.A=self._nz(self.X); return 2
        if op==0x9A: self.SP=self.X; return 2
        if op==0x98: self.A=self._nz(self.Y); return 2
        # Stack
        if op==0x48: self._push(self.A); return 3
        if op==0x68: self.A=self._nz(self._pop()); return 4
        if op==0x08: self._push(self.P|0x30); return 3
        if op==0x28: self.P=(self._pop()&0xEF)|0x20; return 4
        # JMP / JSR / RTS / RTI / BRK
        if op==0x4C: a,_=self._abs(); self.PC=a; return 3
        if op==0x6C: a,_=self._abs(); self.PC=self._r16b(a); return 5
        if op==0x20:
            a,_=self._abs()
            self._push((self.PC-1)>>8); self._push((self.PC-1)&0xFF)
            self.PC=a; return 6
        if op==0x60:
            lo=self._pop(); hi=self._pop()
            self.PC=((hi<<8)|lo+1)&0xFFFF; return 6
        if op==0x40:
            self.P=(self._pop()&0xEF)|0x20
            lo=self._pop(); hi=self._pop()
            self.PC=(hi<<8)|lo; return 6
        if op==0x00:
            self.PC=(self.PC+1)&0xFFFF
            self._push(self.PC>>8); self._push(self.PC&0xFF)
            self._push(self.P|0x30); self._sf(self.I,True)
            self.PC=self._r16(0xFFFE); return 7
        # Branches
        if op==0x90: return self._branch(not self._gf(self.C))
        if op==0xB0: return self._branch(self._gf(self.C))
        if op==0xF0: return self._branch(self._gf(self.Z))
        if op==0xD0: return self._branch(not self._gf(self.Z))
        if op==0x10: return self._branch(not self._gf(self.N))
        if op==0x30: return self._branch(self._gf(self.N))
        if op==0x50: return self._branch(not self._gf(self.V))
        if op==0x70: return self._branch(self._gf(self.V))
        # ADC
        if op==0x69: a,p=self._imm(); self._adc(a); return 2
        if op==0x65: a,p=self._zp();  self._adc(self._r(a)); return 3
        if op==0x75: a,p=self._zpx(); self._adc(self._r(a)); return 4
        if op==0x6D: a,p=self._abs(); self._adc(self._r(a)); return 4
        if op==0x7D: a,p=self._abx(); self._adc(self._r(a)); return 4+p
        if op==0x79: a,p=self._aby(); self._adc(self._r(a)); return 4+p
        if op==0x61: a,p=self._inx(); self._adc(self._r(a)); return 6
        if op==0x71: a,p=self._iny(); self._adc(self._r(a)); return 5+p
        # SBC
        if op==0xE9: a,p=self._imm(); self._sbc(a); return 2
        if op==0xEB: a,p=self._imm(); self._sbc(a); return 2   # unofficial
        if op==0xE5: a,p=self._zp();  self._sbc(self._r(a)); return 3
        if op==0xF5: a,p=self._zpx(); self._sbc(self._r(a)); return 4
        if op==0xED: a,p=self._abs(); self._sbc(self._r(a)); return 4
        if op==0xFD: a,p=self._abx(); self._sbc(self._r(a)); return 4+p
        if op==0xF9: a,p=self._aby(); self._sbc(self._r(a)); return 4+p
        if op==0xE1: a,p=self._inx(); self._sbc(self._r(a)); return 6
        if op==0xF1: a,p=self._iny(); self._sbc(self._r(a)); return 5+p
        # AND
        if op==0x29: a,p=self._imm(); self._and(a); return 2
        if op==0x25: a,p=self._zp();  self._and(self._r(a)); return 3
        if op==0x35: a,p=self._zpx(); self._and(self._r(a)); return 4
        if op==0x2D: a,p=self._abs(); self._and(self._r(a)); return 4
        if op==0x3D: a,p=self._abx(); self._and(self._r(a)); return 4+p
        if op==0x39: a,p=self._aby(); self._and(self._r(a)); return 4+p
        if op==0x21: a,p=self._inx(); self._and(self._r(a)); return 6
        if op==0x31: a,p=self._iny(); self._and(self._r(a)); return 5+p
        # ORA
        if op==0x09: a,p=self._imm(); self._ora(a); return 2
        if op==0x05: a,p=self._zp();  self._ora(self._r(a)); return 3
        if op==0x15: a,p=self._zpx(); self._ora(self._r(a)); return 4
        if op==0x0D: a,p=self._abs(); self._ora(self._r(a)); return 4
        if op==0x1D: a,p=self._abx(); self._ora(self._r(a)); return 4+p
        if op==0x19: a,p=self._aby(); self._ora(self._r(a)); return 4+p
        if op==0x01: a,p=self._inx(); self._ora(self._r(a)); return 6
        if op==0x11: a,p=self._iny(); self._ora(self._r(a)); return 5+p
        # EOR
        if op==0x49: a,p=self._imm(); self._eor(a); return 2
        if op==0x45: a,p=self._zp();  self._eor(self._r(a)); return 3
        if op==0x55: a,p=self._zpx(); self._eor(self._r(a)); return 4
        if op==0x4D: a,p=self._abs(); self._eor(self._r(a)); return 4
        if op==0x5D: a,p=self._abx(); self._eor(self._r(a)); return 4+p
        if op==0x59: a,p=self._aby(); self._eor(self._r(a)); return 4+p
        if op==0x41: a,p=self._inx(); self._eor(self._r(a)); return 6
        if op==0x51: a,p=self._iny(); self._eor(self._r(a)); return 5+p
        # CMP
        if op==0xC9: a,p=self._imm(); self._cmp(self.A,a); return 2
        if op==0xC5: a,p=self._zp();  self._cmp(self.A,self._r(a)); return 3
        if op==0xD5: a,p=self._zpx(); self._cmp(self.A,self._r(a)); return 4
        if op==0xCD: a,p=self._abs(); self._cmp(self.A,self._r(a)); return 4
        if op==0xDD: a,p=self._abx(); self._cmp(self.A,self._r(a)); return 4+p
        if op==0xD9: a,p=self._aby(); self._cmp(self.A,self._r(a)); return 4+p
        if op==0xC1: a,p=self._inx(); self._cmp(self.A,self._r(a)); return 6
        if op==0xD1: a,p=self._iny(); self._cmp(self.A,self._r(a)); return 5+p
        # CPX
        if op==0xE0: a,p=self._imm(); self._cmp(self.X,a); return 2
        if op==0xE4: a,p=self._zp();  self._cmp(self.X,self._r(a)); return 3
        if op==0xEC: a,p=self._abs(); self._cmp(self.X,self._r(a)); return 4
        # CPY
        if op==0xC0: a,p=self._imm(); self._cmp(self.Y,a); return 2
        if op==0xC4: a,p=self._zp();  self._cmp(self.Y,self._r(a)); return 3
        if op==0xCC: a,p=self._abs(); self._cmp(self.Y,self._r(a)); return 4
        # BIT
        if op==0x24: a,p=self._zp();  self._bit(self._r(a)); return 3
        if op==0x2C: a,p=self._abs(); self._bit(self._r(a)); return 4
        # ASL
        if op==0x0A: self.A=self._asl(self.A); return 2
        if op==0x06: a,p=self._zp();  v=self._asl(self._r(a)); self._w(a,v); return 5
        if op==0x16: a,p=self._zpx(); v=self._asl(self._r(a)); self._w(a,v); return 6
        if op==0x0E: a,p=self._abs(); v=self._asl(self._r(a)); self._w(a,v); return 6
        if op==0x1E: a,p=self._abx(); v=self._asl(self._r(a)); self._w(a,v); return 7
        # LSR
        if op==0x4A: self.A=self._lsr(self.A); return 2
        if op==0x46: a,p=self._zp();  v=self._lsr(self._r(a)); self._w(a,v); return 5
        if op==0x56: a,p=self._zpx(); v=self._lsr(self._r(a)); self._w(a,v); return 6
        if op==0x4E: a,p=self._abs(); v=self._lsr(self._r(a)); self._w(a,v); return 6
        if op==0x5E: a,p=self._abx(); v=self._lsr(self._r(a)); self._w(a,v); return 7
        # ROL
        if op==0x2A: self.A=self._rol(self.A); return 2
        if op==0x26: a,p=self._zp();  v=self._rol(self._r(a)); self._w(a,v); return 5
        if op==0x36: a,p=self._zpx(); v=self._rol(self._r(a)); self._w(a,v); return 6
        if op==0x2E: a,p=self._abs(); v=self._rol(self._r(a)); self._w(a,v); return 6
        if op==0x3E: a,p=self._abx(); v=self._rol(self._r(a)); self._w(a,v); return 7
        # ROR
        if op==0x6A: self.A=self._ror(self.A); return 2
        if op==0x66: a,p=self._zp();  v=self._ror(self._r(a)); self._w(a,v); return 5
        if op==0x76: a,p=self._zpx(); v=self._ror(self._r(a)); self._w(a,v); return 6
        if op==0x6E: a,p=self._abs(); v=self._ror(self._r(a)); self._w(a,v); return 6
        if op==0x7E: a,p=self._abx(); v=self._ror(self._r(a)); self._w(a,v); return 7
        # INC
        if op==0xE6: a,p=self._zp();  v=(self._r(a)+1)&0xFF; self._w(a,v); self._nz(v); return 5
        if op==0xF6: a,p=self._zpx(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._nz(v); return 6
        if op==0xEE: a,p=self._abs(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._nz(v); return 6
        if op==0xFE: a,p=self._abx(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._nz(v); return 7
        # DEC
        if op==0xC6: a,p=self._zp();  v=(self._r(a)-1)&0xFF; self._w(a,v); self._nz(v); return 5
        if op==0xD6: a,p=self._zpx(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._nz(v); return 6
        if op==0xCE: a,p=self._abs(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._nz(v); return 6
        if op==0xDE: a,p=self._abx(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._nz(v); return 7
        # INX/INY/DEX/DEY
        if op==0xE8: self.X=(self.X+1)&0xFF; self._nz(self.X); return 2
        if op==0xC8: self.Y=(self.Y+1)&0xFF; self._nz(self.Y); return 2
        if op==0xCA: self.X=(self.X-1)&0xFF; self._nz(self.X); return 2
        if op==0x88: self.Y=(self.Y-1)&0xFF; self._nz(self.Y); return 2
        # Flag ops
        if op==0x18: self._sf(self.C,False); return 2
        if op==0x38: self._sf(self.C,True);  return 2
        if op==0x58: self._sf(self.I,False); return 2
        if op==0x78: self._sf(self.I,True);  return 2
        if op==0xB8: self._sf(self.V,False); return 2
        if op==0xD8: self._sf(self.D,False); return 2
        if op==0xF8: self._sf(self.D,True);  return 2
        # NOP official
        if op==0xEA: return 2
        # NOP unofficial 1-byte
        if op in (0x1A,0x3A,0x5A,0x7A,0xDA,0xFA): return 2
        # NOP unofficial 2-byte
        if op in (0x80,0x82,0x89,0xC2,0xE2): self._imm(); return 2
        if op in (0x04,0x44,0x64): self._zp(); return 3
        if op in (0x14,0x34,0x54,0x74,0xD4,0xF4): self._zpx(); return 4
        if op==0x0C: self._abs(); return 4
        if op in (0x1C,0x3C,0x5C,0x7C,0xDC,0xFC): self._abx(); return 4
        # LAX (unofficial: load A and X)
        if op==0xA7: a,p=self._zp();  v=self._r(a); self.A=self.X=self._nz(v); return 3
        if op==0xB7: a,p=self._zpy(); v=self._r(a); self.A=self.X=self._nz(v); return 4
        if op==0xAF: a,p=self._abs(); v=self._r(a); self.A=self.X=self._nz(v); return 4
        if op==0xBF: a,p=self._aby(); v=self._r(a); self.A=self.X=self._nz(v); return 4+p
        if op==0xA3: a,p=self._inx(); v=self._r(a); self.A=self.X=self._nz(v); return 6
        if op==0xB3: a,p=self._iny(); v=self._r(a); self.A=self.X=self._nz(v); return 5+p
        # SAX (unofficial: store A & X)
        if op==0x87: a,p=self._zp();  self._w(a,self.A&self.X); return 3
        if op==0x97: a,p=self._zpy(); self._w(a,self.A&self.X); return 4
        if op==0x8F: a,p=self._abs(); self._w(a,self.A&self.X); return 4
        if op==0x83: a,p=self._inx(); self._w(a,self.A&self.X); return 6
        # DCP (unofficial: DEC + CMP)
        if op==0xC7: a,p=self._zp();  v=(self._r(a)-1)&0xFF; self._w(a,v); self._cmp(self.A,v); return 5
        if op==0xD7: a,p=self._zpx(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._cmp(self.A,v); return 6
        if op==0xCF: a,p=self._abs(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._cmp(self.A,v); return 6
        if op==0xDF: a,p=self._abx(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._cmp(self.A,v); return 7
        if op==0xDB: a,p=self._aby(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._cmp(self.A,v); return 7
        if op==0xC3: a,p=self._inx(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._cmp(self.A,v); return 8
        if op==0xD3: a,p=self._iny(); v=(self._r(a)-1)&0xFF; self._w(a,v); self._cmp(self.A,v); return 8
        # ISC (unofficial: INC + SBC)
        if op==0xE7: a,p=self._zp();  v=(self._r(a)+1)&0xFF; self._w(a,v); self._sbc(v); return 5
        if op==0xF7: a,p=self._zpx(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._sbc(v); return 6
        if op==0xEF: a,p=self._abs(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._sbc(v); return 6
        if op==0xFF: a,p=self._abx(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._sbc(v); return 7
        if op==0xFB: a,p=self._aby(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._sbc(v); return 7
        if op==0xE3: a,p=self._inx(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._sbc(v); return 8
        if op==0xF3: a,p=self._iny(); v=(self._r(a)+1)&0xFF; self._w(a,v); self._sbc(v); return 8
        # SLO (unofficial: ASL + ORA)
        if op==0x07: a,p=self._zp();  v=self._asl(self._r(a)); self._w(a,v); self._ora(v); return 5
        if op==0x17: a,p=self._zpx(); v=self._asl(self._r(a)); self._w(a,v); self._ora(v); return 6
        if op==0x0F: a,p=self._abs(); v=self._asl(self._r(a)); self._w(a,v); self._ora(v); return 6
        if op==0x1F: a,p=self._abx(); v=self._asl(self._r(a)); self._w(a,v); self._ora(v); return 7
        if op==0x1B: a,p=self._aby(); v=self._asl(self._r(a)); self._w(a,v); self._ora(v); return 7
        if op==0x03: a,p=self._inx(); v=self._asl(self._r(a)); self._w(a,v); self._ora(v); return 8
        if op==0x13: a,p=self._iny(); v=self._asl(self._r(a)); self._w(a,v); self._ora(v); return 8
        # RLA (unofficial: ROL + AND)
        if op==0x27: a,p=self._zp();  v=self._rol(self._r(a)); self._w(a,v); self._and(v); return 5
        if op==0x37: a,p=self._zpx(); v=self._rol(self._r(a)); self._w(a,v); self._and(v); return 6
        if op==0x2F: a,p=self._abs(); v=self._rol(self._r(a)); self._w(a,v); self._and(v); return 6
        if op==0x3F: a,p=self._abx(); v=self._rol(self._r(a)); self._w(a,v); self._and(v); return 7
        if op==0x3B: a,p=self._aby(); v=self._rol(self._r(a)); self._w(a,v); self._and(v); return 7
        if op==0x23: a,p=self._inx(); v=self._rol(self._r(a)); self._w(a,v); self._and(v); return 8
        if op==0x33: a,p=self._iny(); v=self._rol(self._r(a)); self._w(a,v); self._and(v); return 8
        # SRE (unofficial: LSR + EOR)
        if op==0x47: a,p=self._zp();  v=self._lsr(self._r(a)); self._w(a,v); self._eor(v); return 5
        if op==0x57: a,p=self._zpx(); v=self._lsr(self._r(a)); self._w(a,v); self._eor(v); return 6
        if op==0x4F: a,p=self._abs(); v=self._lsr(self._r(a)); self._w(a,v); self._eor(v); return 6
        if op==0x5F: a,p=self._abx(); v=self._lsr(self._r(a)); self._w(a,v); self._eor(v); return 7
        if op==0x5B: a,p=self._aby(); v=self._lsr(self._r(a)); self._w(a,v); self._eor(v); return 7
        if op==0x43: a,p=self._inx(); v=self._lsr(self._r(a)); self._w(a,v); self._eor(v); return 8
        if op==0x53: a,p=self._iny(); v=self._lsr(self._r(a)); self._w(a,v); self._eor(v); return 8
        # RRA (unofficial: ROR + ADC)
        if op==0x67: a,p=self._zp();  v=self._ror(self._r(a)); self._w(a,v); self._adc(v); return 5
        if op==0x77: a,p=self._zpx(); v=self._ror(self._r(a)); self._w(a,v); self._adc(v); return 6
        if op==0x6F: a,p=self._abs(); v=self._ror(self._r(a)); self._w(a,v); self._adc(v); return 6
        if op==0x7F: a,p=self._abx(); v=self._ror(self._r(a)); self._w(a,v); self._adc(v); return 7
        if op==0x7B: a,p=self._aby(); v=self._ror(self._r(a)); self._w(a,v); self._adc(v); return 7
        if op==0x63: a,p=self._inx(); v=self._ror(self._r(a)); self._w(a,v); self._adc(v); return 8
        if op==0x73: a,p=self._iny(); v=self._ror(self._r(a)); self._w(a,v); self._adc(v); return 8
        # Unknown — treat as 2-cycle NOP
        return 2

    def step(self):
        if self.stall > 0:
            self.stall -= 1; self.cycles += 1; return 1

        if self.nmi_pending:
            self._push(self.PC >> 8); self._push(self.PC & 0xFF)
            self._push(self.P & ~self.B)
            self._sf(self.I, True)
            self.PC = self._r16(0xFFFA)
            self.nmi_pending = False
            self.cycles += 7; return 7

        if self.irq_pending and not self._gf(self.I):
            self._push(self.PC >> 8); self._push(self.PC & 0xFF)
            self._push(self.P & ~self.B)
            self._sf(self.I, True)
            self.PC = self._r16(0xFFFE)
            self.irq_pending = False
            self.cycles += 7; return 7

        op = self._r(self.PC); self.PC = (self.PC + 1) & 0xFFFF
        cyc = self._exec(op)
        self.cycles += cyc; return cyc

    def run(self, target):
        spent = 0
        while spent < target:
            spent += self.step()


# ── Bus ───────────────────────────────────────────────────────────────────────
class Bus:
    def __init__(self):
        self.ram    = bytearray(0x800)
        self.ppu    = None
        self.mapper = None
        self.cpu    = None
        self.ctrl1  = 0          # live button state
        self.ctrl1_latch = 0     # shift register
        self.strobe = 0

    def read(self, addr):
        addr &= 0xFFFF
        if addr < 0x2000:    return self.ram[addr & 0x7FF]
        if addr < 0x4000:    return self.ppu.read_reg(addr & 7)
        if addr == 0x4016:
            if self.strobe:  return (self.ctrl1 & 1) | 0x40
            v = (self.ctrl1_latch & 1) | 0x40
            self.ctrl1_latch >>= 1; return v
        if addr == 0x4017:   return 0x40
        if addr < 0x4020:    return 0
        if addr >= 0x8000:   return self.mapper.read_prg(addr)
        return 0

    def write(self, addr, val):
        addr &= 0xFFFF; val &= 0xFF
        if addr < 0x2000:
            self.ram[addr & 0x7FF] = val; return
        if addr < 0x4000:
            self.ppu.write_reg(addr & 7, val); return
        if addr == 0x4014:
            page = val << 8
            data = bytearray(self.ram[page & 0x7FF:(page & 0x7FF)+256]) \
                   if page < 0x800 \
                   else bytearray(self.read(page + i) for i in range(256))
            self.ppu.oam_dma(data)
            self.cpu.stall += 513 + (self.cpu.cycles & 1)
            return
        if addr == 0x4016:
            if self.strobe and not (val & 1):
                self.ctrl1_latch = self.ctrl1
            self.strobe = val & 1; return
        if addr >= 0x8000:
            self.mapper.write_prg(addr, val)


# ── GUI ───────────────────────────────────────────────────────────────────────
class ACNESEmulator:
    TITLE = "ACNESEMU 0.2 — A.C Holdings / Team Flames"

    def __init__(self, root):
        self.root = root
        root.title(self.TITLE)
        root.configure(bg="#0d0d1a")
        root.resizable(False, False)

        self.bus = Bus()
        self.ppu = PPU()
        self.cpu = CPU(self.bus)
        self.bus.ppu = self.ppu
        self.bus.cpu = self.cpu

        self.rom_loaded = False
        self.running    = False
        self._stop      = False
        self.ctrl_state = 0

        self._build_ui()
        self._bind_keys()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self.root, bg="#0d0d1a")
        hdr.pack(fill="x", padx=6, pady=(6,0))
        tk.Label(hdr, text="ACNESEMU 0.2", bg="#0d0d1a",
                 fg="#e94560", font=("Courier", 11, "bold")).pack(side="left")
        tk.Label(hdr, text="A.C Holdings / Team Flames © 1999–2026",
                 bg="#0d0d1a", fg="#44446a", font=("Courier", 8)).pack(side="right")

        self.canvas = tk.Canvas(self.root, width=512, height=480,
                               bg="black", highlightthickness=1,
                               highlightbackground="#e94560")
        self.canvas.pack(padx=6, pady=4)

        self.status_lbl = tk.Label(
            self.root, text="No ROM loaded — File › Load ROM",
            bg="#0d0d1a", fg="#666680", font=("Courier", 9))
        self.status_lbl.pack(pady=(0,6))

        self._build_menu()

    def _build_menu(self):
        m = Menu(self.root, bg="#1a1a2e", fg="white", activebackground="#e94560",
                 activeforeground="white", tearoff=0)
        self.root.config(menu=m)

        fm = Menu(m, bg="#1a1a2e", fg="white", activebackground="#e94560",
                  activeforeground="white", tearoff=0)
        m.add_cascade(label="File", menu=fm)
        fm.add_command(label="Load ROM …",  command=self.load_rom)
        fm.add_command(label="Reset",        command=self.soft_reset)
        fm.add_separator()
        fm.add_command(label="Exit",         command=self.on_close)

        hm = Menu(m, bg="#1a1a2e", fg="white", activebackground="#e94560",
                  activeforeground="white", tearoff=0)
        m.add_cascade(label="Help", menu=hm)
        hm.add_command(label="Controls …",  command=self._show_help)

    def _show_help(self):
        messagebox.showinfo("Controls — ACNESEMU 0.2",
            "Z / X        →  A / B\n"
            "Enter        →  Start\n"
            "Right Shift  →  Select\n"
            "Arrow keys   →  D-Pad\n\n"
            "Mapper support: 0 (NROM), 1 (MMC1),\n"
            "                2 (UxROM), 3 (CNROM)\n\n"
            "Note: Pure Python — expect ~15-30 fps.\n"
            "Use PyPy for near full speed.")

    # ── Input ─────────────────────────────────────────────────────────────────
    def _bind_keys(self):
        # NES bit order: A=0 B=1 Sel=2 Start=3 Up=4 Down=5 Left=6 Right=7
        binds = {
            "z":7, "Z":7,                          # A (bit 0 when serialized)
            "x":6, "X":6,                          # B
            "Shift_R":5, "Shift_L":5,              # Select
            "Return":4,                            # Start
            "Up":3, "Down":2, "Left":1, "Right":0 # D-Pad
        }
        for k, bit in binds.items():
            self.root.bind(f"<KeyPress-{k}>",   lambda e, b=bit: self._btn(b, True))
            self.root.bind(f"<KeyRelease-{k}>", lambda e, b=bit: self._btn(b, False))

    def _btn(self, bit, pressed):
        if pressed: self.ctrl_state |= (1 << bit)
        else:       self.ctrl_state &= ~(1 << bit)
        self.bus.ctrl1 = self.ctrl_state & 0xFF

    # ── ROM loading ───────────────────────────────────────────────────────────
    def load_rom(self):
        path = filedialog.askopenfilename(
            title="Load NES ROM",
            filetypes=[("NES ROM", "*.nes"), ("All files", "*.*")])
        if not path: return
        try:
            with open(path, "rb") as f: data = f.read()
            self._parse_ines(data)
            name = os.path.basename(path)
            self.status_lbl.config(text=f"▶  {name}")
            self.root.title(f"{self.TITLE}  |  {name}")
            if not self.running:
                self._stop   = False
                self.running = True
                threading.Thread(target=self._emu_loop, daemon=True).start()
        except Exception as ex:
            messagebox.showerror("Load Error", str(ex))

    def _parse_ines(self, data):
        if data[:4] != b"NES\x1a":
            raise ValueError("Not a valid iNES ROM (bad header)")
        prg_n   = data[4]; chr_n = data[5]
        flags6  = data[6]; flags7 = data[7]
        mid     = (flags7 & 0xF0) | (flags6 >> 4)
        mirror  = MIR_4 if (flags6 & 8) else (MIR_V if (flags6 & 1) else MIR_H)
        offset  = 16 + (512 if (flags6 & 4) else 0)
        prg     = data[offset: offset + prg_n * 16384]
        chr_d   = data[offset + prg_n * 16384: offset + prg_n * 16384 + chr_n * 8192]

        mapper = make_mapper(mid, prg, chr_d, mirror)
        self.bus.mapper = mapper
        self.ppu.connect(mapper)

        self.bus.ram = bytearray(0x800)
        self.cpu.reset()
        self.ppu.reset()
        self.rom_loaded = True

    def soft_reset(self):
        if self.rom_loaded:
            self.cpu.reset()
            self.ppu.reset()

    # ── Emulation loop ────────────────────────────────────────────────────────
    def _emu_loop(self):
        FRAME_CYCLES = 29780
        frame_dur    = 1.0 / 60.0
        last         = time.perf_counter()

        while not self._stop:
            if not self.rom_loaded:
                time.sleep(0.016); continue

            # 1. Render PPU frame using current PPU state
            nmi = self.ppu.tick_frame()

            # 2. Fire NMI so the game can update OAM / scroll for next frame
            if nmi:
                self.cpu.trigger_nmi()

            # 3. Run CPU for one frame worth of cycles
            self.cpu.run(FRAME_CYCLES)

            # 4. Send framebuffer to display thread
            frame = bytes(self.ppu.fb)
            self.root.after_idle(self._blit, frame)

            # 5. Throttle to 60 fps
            now     = time.perf_counter()
            elapsed = now - last
            sleep   = frame_dur - elapsed
            if sleep > 0.001:
                time.sleep(sleep)
            last = time.perf_counter()

    def _blit(self, fb):
        # Convert NES palette indices → RGB bytes
        rgb = bytearray(256 * 240 * 3)
        for i in range(256 * 240):
            j = (fb[i] & 0x3F) * 3
            rgb[i*3]   = _PAL_RGB[j]
            rgb[i*3+1] = _PAL_RGB[j+1]
            rgb[i*3+2] = _PAL_RGB[j+2]
        img   = Image.frombytes("RGB", (256, 240), bytes(rgb))
        img   = img.resize((512, 480), Image.NEAREST)
        photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=photo)
        self.canvas.image = photo   # keep ref

    def on_close(self):
        self._stop = True
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app  = ACNESEmulator(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
