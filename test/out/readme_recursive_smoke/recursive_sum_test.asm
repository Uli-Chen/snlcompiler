.data
g_result: .word 0

.text
.globl main
j main
proc_sumTo:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
addi $t0, $fp, 8
lw $t1, 0($t0)
li $t0, 1
bge $t1, $t0, else_2
lw $t0, 12($fp)
lw $t1, 12($fp)
lw $t2, 0($t1)
sw $t2, 0($t0)
j endif_3
else_2:
lw $t0, 12($fp)
lw $t2, 12($fp)
lw $t1, 0($t2)
addi $t2, $fp, 8
lw $t3, 0($t2)
add $t1, $t1, $t3
sw $t1, 0($t0)
addi $t0, $fp, 8
lw $t1, 0($t0)
li $t0, 1
sub $t1, $t1, $t0
lw $t0, 12($fp)
addi $sp, $sp, -4
sw $t0, 0($sp)
addi $sp, $sp, -4
sw $t1, 0($sp)
jal proc_sumTo
addi $sp, $sp, 8
endif_3:
lw $t1, 12($fp)
lw $t0, 0($t1)
j proc_sumTo_end_1
proc_sumTo_end_1:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
main:
la $t0, g_result
li $t1, 0
sw $t1, 0($t0)
li $t0, 5
la $t1, g_result
addi $sp, $sp, -4
sw $t1, 0($sp)
addi $sp, $sp, -4
sw $t0, 0($sp)
jal proc_sumTo
addi $sp, $sp, 8
la $t0, g_result
lw $t1, 0($t0)
move $a0, $t1
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
li $v0, 10
syscall
