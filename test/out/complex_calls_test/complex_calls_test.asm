.data
g_n: .word 0
g_fibResult: .word 0
g_factResult: .word 0
g_total: .word 0
g_scaled: .word 0
g_values: .space 16
g_report: .space 16

.text
.globl main
j main
proc_add:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
lw $t0, 16($fp)
addi $t1, $fp, 8
lw $t2, 0($t1)
addi $t1, $fp, 12
lw $t3, 0($t1)
add $t2, $t2, $t3
sw $t2, 0($t0)
lw $t0, 16($fp)
lw $t2, 0($t0)
j proc_add_end_1
proc_add_end_1:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
proc_scale:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
lw $t2, 16($fp)
addi $t0, $fp, 8
lw $t3, 0($t0)
addi $t0, $fp, 12
lw $t1, 0($t0)
mul $t3, $t3, $t1
sw $t3, 0($t2)
lw $t2, 16($fp)
lw $t3, 0($t2)
j proc_scale_end_2
proc_scale_end_2:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
proc_factorial:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
addi $sp, $sp, -4
addi $t3, $fp, 8
lw $t2, 0($t3)
li $t3, 2
bge $t2, $t3, else_4
lw $t3, 12($fp)
li $t2, 1
sw $t2, 0($t3)
j endif_5
else_4:
addi $t3, $fp, 8
lw $t2, 0($t3)
li $t3, 1
sub $t2, $t2, $t3
addi $t3, $fp, -4
addi $sp, $sp, -4
sw $t3, 0($sp)
addi $sp, $sp, -4
sw $t2, 0($sp)
jal proc_factorial
addi $sp, $sp, 8
lw $t2, 12($fp)
addi $t3, $fp, 8
lw $t1, 0($t3)
addi $t3, $fp, -4
lw $t0, 0($t3)
mul $t1, $t1, $t0
sw $t1, 0($t2)
endif_5:
lw $t2, 12($fp)
lw $t1, 0($t2)
j proc_factorial_end_3
proc_factorial_end_3:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
proc_fib:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
addi $sp, $sp, -8
addi $t1, $fp, 8
lw $t2, 0($t1)
li $t1, 2
bge $t2, $t1, else_7
lw $t1, 12($fp)
addi $t2, $fp, 8
lw $t0, 0($t2)
sw $t0, 0($t1)
j endif_8
else_7:
addi $t1, $fp, 8
lw $t0, 0($t1)
li $t1, 1
sub $t0, $t0, $t1
addi $t1, $fp, -4
addi $sp, $sp, -4
sw $t1, 0($sp)
addi $sp, $sp, -4
sw $t0, 0($sp)
jal proc_fib
addi $sp, $sp, 8
addi $t0, $fp, 8
lw $t1, 0($t0)
li $t0, 2
sub $t1, $t1, $t0
addi $t0, $fp, -8
addi $sp, $sp, -4
sw $t0, 0($sp)
addi $sp, $sp, -4
sw $t1, 0($sp)
jal proc_fib
addi $sp, $sp, 8
addi $t1, $fp, -4
lw $t0, 0($t1)
addi $t1, $fp, -8
lw $t2, 0($t1)
lw $t1, 12($fp)
addi $sp, $sp, -4
sw $t1, 0($sp)
addi $sp, $sp, -4
sw $t2, 0($sp)
addi $sp, $sp, -4
sw $t0, 0($sp)
jal proc_add
addi $sp, $sp, 12
endif_8:
lw $t0, 12($fp)
lw $t2, 0($t0)
j proc_fib_end_6
proc_fib_end_6:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
proc_storeReport:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
lw $t2, 24($fp)
addi $t0, $fp, 8
lw $t1, 0($t0)
sw $t1, 0($t2)
lw $t2, 24($fp)
addi $t2, $t2, 4
addi $t1, $fp, 12
lw $t0, 0($t1)
sw $t0, 0($t2)
lw $t2, 24($fp)
addi $t2, $t2, 8
addi $t0, $fp, 16
lw $t1, 0($t0)
sw $t1, 0($t2)
lw $t2, 24($fp)
addi $t2, $t2, 12
addi $t1, $fp, 20
lw $t0, 0($t1)
sw $t0, 0($t2)
addi $t2, $fp, 16
lw $t0, 0($t2)
j proc_storeReport_end_9
proc_storeReport_end_9:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
proc_printReport:
addi $sp, $sp, -8
sw $fp, 0($sp)
sw $ra, 4($sp)
move $fp, $sp
lw $t0, 8($fp)
lw $t2, 0($t0)
move $a0, $t2
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
lw $t2, 8($fp)
addi $t2, $t2, 4
lw $t0, 0($t2)
move $a0, $t0
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
lw $t0, 8($fp)
addi $t0, $t0, 8
lw $t2, 0($t0)
move $a0, $t2
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
lw $t2, 8($fp)
addi $t2, $t2, 12
lw $t0, 0($t2)
move $a0, $t0
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
lw $t0, 8($fp)
addi $t0, $t0, 12
lw $t2, 0($t0)
j proc_printReport_end_10
proc_printReport_end_10:
move $sp, $fp
lw $fp, 0($sp)
lw $ra, 4($sp)
addi $sp, $sp, 8
jr $ra
main:
la $t2, g_n
li $t0, 6
sw $t0, 0($t2)
la $t2, g_n
lw $t0, 0($t2)
la $t2, g_fibResult
addi $sp, $sp, -4
sw $t2, 0($sp)
addi $sp, $sp, -4
sw $t0, 0($sp)
jal proc_fib
addi $sp, $sp, 8
li $t0, 5
la $t2, g_factResult
addi $sp, $sp, -4
sw $t2, 0($sp)
addi $sp, $sp, -4
sw $t0, 0($sp)
jal proc_factorial
addi $sp, $sp, 8
la $t0, g_fibResult
lw $t2, 0($t0)
la $t0, g_factResult
lw $t1, 0($t0)
la $t0, g_total
addi $sp, $sp, -4
sw $t0, 0($sp)
addi $sp, $sp, -4
sw $t1, 0($sp)
addi $sp, $sp, -4
sw $t2, 0($sp)
jal proc_add
addi $sp, $sp, 12
la $t2, g_total
lw $t1, 0($t2)
li $t2, 2
la $t0, g_scaled
addi $sp, $sp, -4
sw $t0, 0($sp)
addi $sp, $sp, -4
sw $t2, 0($sp)
addi $sp, $sp, -4
sw $t1, 0($sp)
jal proc_scale
addi $sp, $sp, 12
la $t1, g_values
li $t2, 1
addi $t2, $t2, -1
li $t0, 4
mul $t2, $t2, $t0
add $t1, $t1, $t2
la $t2, g_fibResult
lw $t0, 0($t2)
sw $t0, 0($t1)
la $t1, g_values
li $t0, 2
addi $t0, $t0, -1
li $t2, 4
mul $t0, $t0, $t2
add $t1, $t1, $t0
la $t0, g_factResult
lw $t2, 0($t0)
sw $t2, 0($t1)
la $t1, g_values
li $t2, 3
addi $t2, $t2, -1
li $t0, 4
mul $t2, $t2, $t0
add $t1, $t1, $t2
la $t2, g_total
lw $t0, 0($t2)
sw $t0, 0($t1)
la $t1, g_values
li $t0, 4
addi $t0, $t0, -1
li $t2, 4
mul $t0, $t0, $t2
add $t1, $t1, $t0
la $t0, g_scaled
lw $t2, 0($t0)
sw $t2, 0($t1)
la $t1, g_values
li $t2, 1
addi $t2, $t2, -1
li $t0, 4
mul $t2, $t2, $t0
add $t1, $t1, $t2
lw $t2, 0($t1)
la $t1, g_values
li $t0, 2
addi $t0, $t0, -1
li $t3, 4
mul $t0, $t0, $t3
add $t1, $t1, $t0
lw $t0, 0($t1)
la $t1, g_values
li $t3, 3
addi $t3, $t3, -1
li $t4, 4
mul $t3, $t3, $t4
add $t1, $t1, $t3
lw $t3, 0($t1)
la $t1, g_values
li $t4, 4
addi $t4, $t4, -1
li $t5, 4
mul $t4, $t4, $t5
add $t1, $t1, $t4
lw $t4, 0($t1)
la $t1, g_report
addi $sp, $sp, -4
sw $t1, 0($sp)
addi $sp, $sp, -4
sw $t4, 0($sp)
addi $sp, $sp, -4
sw $t3, 0($sp)
addi $sp, $sp, -4
sw $t0, 0($sp)
addi $sp, $sp, -4
sw $t2, 0($sp)
jal proc_storeReport
addi $sp, $sp, 20
la $t2, g_report
addi $sp, $sp, -4
sw $t2, 0($sp)
jal proc_printReport
addi $sp, $sp, 4
li $v0, 10
syscall
