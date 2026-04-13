.data
g_result: .word 0

.text
.globl main
j main
proc_mirrorSum:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
addi $sp, $sp, -4
addi $t0, $fp, -4
addi $t1, $fp, 8
lw $t2, 0($t1)
sw $t2, 0($t0)
addi $t0, $fp, -4
lw $t2, 0($t0)
li $t0, 1
bge $t2, $t0, else_2
lw $t0, 12($fp)
lw $t2, 12($fp)
lw $t1, 0($t2)
sw $t1, 0($t0)
j endif_3
else_2:
lw $t0, 12($fp)
lw $t1, 12($fp)
lw $t2, 0($t1)
addi $t1, $fp, -4
lw $t3, 0($t1)
add $t2, $t2, $t3
sw $t2, 0($t0)
addi $t0, $fp, -4
lw $t2, 0($t0)
li $t0, 1
sub $t2, $t2, $t0
lw $t0, 12($fp)
addi $sp, $sp, -4
sw $t0, 0($sp)
addi $sp, $sp, -4
sw $t2, 0($sp)
jal proc_mirrorSum
addi $sp, $sp, 8
lw $t2, 12($fp)
lw $t0, 12($fp)
lw $t3, 0($t0)
addi $t0, $fp, -4
lw $t1, 0($t0)
add $t3, $t3, $t1
sw $t3, 0($t2)
endif_3:
lw $t2, 12($fp)
lw $t3, 0($t2)
j proc_mirrorSum_end_1
proc_mirrorSum_end_1:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
main:
la $t3, g_result
li $t2, 0
sw $t2, 0($t3)
li $t3, 3
la $t2, g_result
addi $sp, $sp, -4
sw $t2, 0($sp)
addi $sp, $sp, -4
sw $t3, 0($sp)
jal proc_mirrorSum
addi $sp, $sp, 8
la $t3, g_result
lw $t2, 0($t3)
move $a0, $t2
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
li $v0, 10
syscall
