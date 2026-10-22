// HackTwin benchmark workload (HackTwin-authored).
// RAM[1] = 1 + 2 + ... + RAM[0]   (RAM[0] >= 0)
    @R1
    M=0
    @R0
    D=M
    @n
    M=D
(LOOP)
    @n
    D=M
    @END
    D;JEQ
    @R1
    M=D+M
    @n
    M=M-1
    @LOOP
    0;JMP
(END)
    @END
    0;JMP
