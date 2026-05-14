.data
g_x: .word 0
tmp_main_t0: .word 0
tmp_main_t1: .word 0
tmp_main_t2: .word 0
tmp_main_t3: .word 0
tmp_main_t4: .word 0

.text
.globl main
j main
main:
li $t0, 0
la $t1, tmp_main_t0
sw $t0, 0($t1)
li $t0, 2
la $t1, tmp_main_t1
sw $t0, 0($t1)
la $t0, g_x
la $t1, tmp_main_t2
sw $t0, 0($t1)
li $t0, 2
la $t2, tmp_main_t2
lw $t1, 0($t2)
sw $t0, 0($t1)
la $t1, g_x
la $t0, tmp_main_t3
sw $t1, 0($t0)
la $t0, tmp_main_t3
lw $t1, 0($t0)
lw $t0, 0($t1)
la $t2, tmp_main_t4
sw $t0, 0($t2)
la $t1, tmp_main_t4
lw $t0, 0($t1)
move $a0, $t0
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
li $v0, 10
syscall
