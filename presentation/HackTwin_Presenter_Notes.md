# HackTwin review — presenter notes

Ten slides. Each section has the slide text summary, a speaker script, sources and one likely viva question. The same text is in each slide's speaker notes in `HackTwin_Review.pptx`.


## Slide 1: HackTwin (title slide)

**Speaker Script**
Good morning. Our project is HackTwin. In the Elements of Computing course we build a small computer, the Hack computer, by describing its chips in a hardware description language. HackTwin is a tool that runs those computer designs automatically in the official simulator, keeps a record of everything that happened, and helps us compare designs and understand where time is spent. Keep this one sentence in mind: run, record, store, analyse, compare, show.

**Sources / Evidence**
Team names, roll numbers and course code from slide 1 of HackTwin_MidTerm_Review.pptx. One-sentence summary matches hacktwin_run/README.md.

**Likely Viva Question**

Q: Is HackTwin a digital twin of a physical chip?

A: No. It is a software record of simulated HDL designs. Nothing is measured on real hardware, so it says nothing about real speed, area or power.


## Slide 2: HackTwin (title slide)

**Speaker Script**
Let me start with the problem. Our CPU designs are text files in HDL. To test one, we run a small benchmark program in the simulator, and it prints one line per clock cycle. Here is real output for adding 2 and 3: the answer 5 appears in RAM zero at cycle 6. With several designs, ten programs and repeats, nobody can read all of this by hand, check every answer, and compare designs fairly. HackTwin automates that.

**Sources / Evidence**
ComputerAdd.out from the repository; terms defined per hacktwin_run/README.md.

**Likely Viva Question**

Q: Why not just look at the simulator's pass/fail message?

A: Pass/fail tells you only whether the output matched the expected file. It does not tell you how many cycles a program took, where they were spent, or how two designs differ.


## Slide 3: HackTwin (title slide)

**Speaker Script**
This is the same literature review as our mid-term deck. Four papers define and survey digital twins: Kritzinger and colleagues, Barricelli and colleagues, Fuller and colleagues, and Jones and colleagues. Two are from computer architecture: gem5, a full-system simulator, and McPAT, which models power, area and timing. We take the idea of a digital twin, a continuously updated virtual record of a system, and apply it to simulated CPU designs. We are honest that, unlike McPAT, we do not estimate power, area or timing.

**Sources / Evidence**
Old deck slide 3 (image media/image2.png): six studies with authors, years and contributions as listed.

**Likely Viva Question**

Q: Your literature talks about twins of physical systems. Why call this a digital twin?

A: We use the term loosely: the 'system' is the simulated HDL design, and the twin is the stored, analysable record of its behaviour. We state clearly that it is not a twin of a physical chip.


## Slide 4: HackTwin (title slide)

**Speaker Script**
So what does HackTwin actually do? It takes three inputs: CPU designs, benchmark programs with known answers, and the official test scripts. It runs every combination in the simulator and records an execution trace, which is just the cycle-by-cycle log you saw. Then it checks the answers, stores everything, analyses it and compares designs. When we say digital twin, we mean that stored record of each design's simulated behaviour. And just as important is what it does not do: no real hardware, no clock speed, no area or power.

**Sources / Evidence**
README.md sections 'HackTwin' intro and 'Known limitations'.

**Likely Viva Question**

Q: What is the difference between a correct design and a faster design?

A: Correct means it produced the expected answers. Faster, in our tool, only means it needed fewer simulated clock cycles on the same program. A design can be correct and slower, as our two-phase design shows.


## Slide 5: HackTwin (title slide)

**Speaker Script**
Here is the whole system as a left-to-right flow. Designs and programs go into the simulator runner. The output trace is parsed and checked against an independent reference model of the Hack instruction set. Results go into a SQLite database, our twin store. Analysis and comparison read from the store, and the dashboard and report present them. The predictor sits beside the store: it learns from stored runs, but its outputs are estimates and are labelled that way. The GitHub dataset simply supplies more designs on the left.

**Sources / Evidence**
File and module names exist in hacktwin_run/; flow described in README.md.

**Likely Viva Question**

Q: Why SQLite instead of files?

A: One file, no server, transactions so a failed load does not leave half the data, and versioned schema migrations so older databases are upgraded instead of lost.


## Slide 6: HackTwin (title slide)

**Speaker Script**
This slide shows how one run works. We wrap the design in a small harness chip so that we can see the CPU's internal signals. The simulator runs the program until it reaches its final loop, writing one row per cycle. We record the program counter, which is the address of the current instruction, the registers and every memory write. Then we check the answer two ways: against the expected value, and against an independent model of the instruction set. Everything goes into the store, including failures, so nothing is silently dropped.

**Sources / Evidence**
Implementation in sim_runner.py and twin_store.py; schema version 3 confirmed by the app status bar.

**Likely Viva Question**

Q: Why do you need three different hashes for a design?

A: A file hash changes even when only a comment changes. The comment-free hash ignores formatting. The netlist hash ignores part order and wire names, so it tells us whether the hardware wiring actually differs. We found design 3 had the same wiring as design 2.


## Slide 7: HackTwin (title slide)

**Speaker Script**
Once runs are stored, we analyse them. Bottleneck analysis shows where cycles go, for example which loop takes most of the time. We are careful to label each finding: an observation is a fact from the trace, a heuristic is a suggestion worth testing, and a confirmed problem is something like a wrong answer. Comparison uses only correct runs of the same program and inputs, and speedup is simply baseline cycles divided by candidate cycles. Ranking uses that one number, with no hidden weights. Recommendations are only called verified when we actually simulated the suggested design.

**Sources / Evidence**
Function names and labels verified in the listed modules; recommendation statuses shown in the dashboard.

**Likely Viva Question**

Q: Is frequent register change a bug?

A: No. In Hack the A register holds both addresses and jump targets, so it changes often in normal code. We report it as an observation, not a defect.


## Slide 8: HackTwin (title slide)

**Speaker Script**
We used GitHub in two ways. First, 50 real Hack programs from six pinned, permissively licensed repositories, used as extra test workloads. Second, other students' CPU designs: we found 1,346 repositories, processed 339, and 208 imported designs passed every test. We never run code from those repositories and we record licences. For machine learning, a model learns from examples to predict a cycle count. Prediction error is the difference between predicted and measured cycles. The best predictor uses the instruction count and each design's measured cycles per instruction: error under one cycle. Models that only read the program text are barely better than guessing the median. These are estimates, not measurements, and they do not transfer to a new kind of design.

**Sources / Evidence**
Six repositories and licences: collect_github_workloads.py. Funnel and MAE numbers: reports/EXPERIMENT_REPORT.md and cycle_predictor_report.json. The guide's older model: MAE 439.5 vs 513.2 median, R² −0.0856 (hacktwin_cycle_model.validation.json).

**Likely Viva Question**

Q: Is your ML model accurate?

A: Only the calibrated instruction-count model is accurate, and only for designs it has measurements for. The text-only ML models are weak. The earlier model in the guide had R² below zero, so we treat it as exploratory.


## Slide 9: HackTwin (title slide)

**Speaker Script**
Here are the measured results. Every number comes from simulator runs stored in the database. All eight of our designs gave correct answers on all 31 test cases. Designs that only rearrange logic, like the XOR-based ALU, took exactly the same cycles as the baseline: an honest null result. Jump folding, which skips repeated jump instructions in loops, cut total cycles by about 6.5 percent. The two-phase design took exactly twice as many cycles, which shows our measurement really detects differences. All 208 correct GitHub designs had baseline timing. What this does not show is that any design is a faster real chip.

**Sources / Evidence**
All values from reports/EXPERIMENT_REPORT.md and reports/experiment_results.json. Note: the old guide's 14/26/64 'cycles' were test-script row counts, identical for every design by construction.

**Likely Viva Question**

Q: Your old slides said designs 1 and 2 both took 14, 26 and 64 cycles. Why are the numbers different now?

A: Those numbers were how many rows the official test script prints, which is fixed by the script, so every design always tied. We now measure cycles until the program actually finishes.


## Slide 10: HackTwin (title slide)

**Speaker Script**
To finish: what is done, what is limited, and what is next. The full pipeline, the analysis, the dashboard and the tests are implemented and passing. The predictor is only reliable once a design has been measured, the GitHub ingestion is partial, and we can only probe a few memory cells per run. Our next steps are processing the remaining repositories, attempting a genuinely pipelined CPU, and, if we want real timing or area numbers, using a synthesis tool, which the simulator cannot provide. In one sentence: HackTwin turns raw simulator output into checked, comparable evidence about Hack CPU designs. Thank you.

**Sources / Evidence**
README.md; final test run: 136 passed (8 skipped), integration 7 passed (1 network test skipped).

**Likely Viva Question**

Q: What would it take to claim a design is really faster?

A: Its clock period and area, from a synthesis or timing tool, combined with our cycle counts. Our simulator gives only cycles, so we make no speed claim.


## Conclusion (say at the end)

HackTwin turns raw nand2tetris simulator output into a checked, stored and comparable record of each Hack CPU design, so we can say with evidence which designs are correct and which use fewer simulated cycles — without claiming anything about real hardware speed, area or power.
