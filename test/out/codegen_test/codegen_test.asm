.data
g_x: .word 0
g_nums: .space 12
g_pair: .space 8
p_addTo_target: .word 0
p_addTo_delta: .word 0

.text
.globl main
j main
proc_addTo:
addi $sp, $sp, -4
sw $ra, 0($sp)
lw $t0, p_addTo_target
lw $t1, p_addTo_target
lw $t2, 0($t1)
la $t1, p_addTo_delta
lw $t3, 0($t1)
add $t2, $t2, $t3
sw $t2, 0($t0)
lw $t0, p_addTo_target
lw $t2, 0($t0)
j proc_addTo_end_1
proc_addTo_end_1:
lw $ra, 0($sp)
addi $sp, $sp, 4
jr $ra
main:
la $t2, g_x
li $t0, 2
sw $t0, 0($t2)
la $t2, g_nums
li $t0, 1
addi $t0, $t0, -1
li $t3, 4
mul $t0, $t0, $t3
add $t2, $t2, $t0
li $t0, 3
sw $t0, 0($t2)
la $t2, g_pair
la $t0, g_x
lw $t3, 0($t0)
la $t0, g_nums
li $t1, 1
addi $t1, $t1, -1
li $t4, 4
mul $t1, $t1, $t4
add $t0, $t0, $t1
lw $t1, 0($t0)
add $t3, $t3, $t1
sw $t3, 0($t2)
la $t2, g_pair
sw $t2, p_addTo_target
li $t2, 4
sw $t2, p_addTo_delta
jal proc_addTo
la $t2, g_pair
lw $t3, 0($t2)
move $a0, $t3
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
la $t3, g_pair
lw $t2, 0($t3)
li $t3, 10
bge $t2, $t3, else_2
li $t3, 1
move $a0, $t3
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
j endif_3
else_2:
li $t3, 0
move $a0, $t3
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
endif_3:
li $v0, 10
syscall
