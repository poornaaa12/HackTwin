// HackTwin benchmark workload (HackTwin-authored).
// RAM[2] = sum of RAM[RAM[1]] .. RAM[RAM[1] + RAM[0] - 1]
// (RAM[0] = element count, RAM[1] = base address).
    @R2
    M=0
    @R1
    D=M
    @p
    M=D
    @R0
    D=M
    @n
    M=D
(LOOP)
    @n
    D=M
    @END
    D;JEQ
    @p
    A=M
    D=M
    @R2
    M=D+M
    @p
    M=M+1
    @n
    M=M-1
    @LOOP
    0;JMP
(END)
    @END
    0;JMP
