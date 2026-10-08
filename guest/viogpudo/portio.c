/* Intrinsèques d'E/S de MSVC que clang-cl ne fournit pas (VirtIOPCILegacy.c et osdep.h). */
unsigned char __inbyte(unsigned short p) { unsigned char v; __asm__ volatile("inb %1, %0" : "=a"(v) : "Nd"(p)); return v; }
unsigned short __inword(unsigned short p) { unsigned short v; __asm__ volatile("inw %1, %0" : "=a"(v) : "Nd"(p)); return v; }
unsigned long __indword(unsigned short p) { unsigned long v; __asm__ volatile("inl %1, %0" : "=a"(v) : "Nd"(p)); return v; }
void __outbyte(unsigned short p, unsigned char v) { __asm__ volatile("outb %0, %1" : : "a"(v), "Nd"(p)); }
void __outword(unsigned short p, unsigned short v) { __asm__ volatile("outw %0, %1" : : "a"(v), "Nd"(p)); }
void __outdword(unsigned short p, unsigned long v) { __asm__ volatile("outl %0, %1" : : "a"(v), "Nd"(p)); }
