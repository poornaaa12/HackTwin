// HackTwin benchmark workload (HackTwin-authored).
// RAM[2] = RAM[0] * RAM[1] by repeated addition (RAM[1] iterations).
    @R2
    M=0
    @R1
    D=M
    @i
    M=D
(LOOP)
    @i
    D=M
    @END
    D;JEQ
    @R0
    D=M
    @R2
    M=D+M
    @i
    M=M-1
    @LOOP
    0;JMP
(END)
    @END
    0;JMP
