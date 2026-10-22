// HackTwin benchmark workload (HackTwin-authored).
// RAM[2] = RAM[0] / RAM[1], RAM[3] = RAM[0] % RAM[1]  (RAM[0] >= 0, RAM[1] > 0)
// by repeated subtraction.
    @R2
    M=0
    @R0
    D=M
    @R3
    M=D
(LOOP)
    @R1
    D=M
    @R3
    D=M-D
    @END
    D;JLT
    @R3
    M=D
    @R2
    M=M+1
    @LOOP
    0;JMP
(END)
    @END
    0;JMP
