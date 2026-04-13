.data
g_count: .word 0
g_total: .word 0
g_mark: .word 0
g_letters: .space 40
g_pair: .space 20
p_update_value: .word 0
p_update_flag: .word 0
p_update_i: .word 0

.text
.globl main
j main
proc_update:
addi $sp, $sp, -4
sw $ra, 0($sp)
la $t0, p_update_i
li $t1, 1
sw $t1, 0($t0)
while_2:
la $t0, p_update_i
lw $t1, 0($t0)
li $t0, 10
bge $t1, $t0, endwhile_3
lw $t0, p_update_value
lw $t1, p_update_value
lw $t2, 0($t1)
la $t1, p_update_i
lw $t3, 0($t1)
add $t2, $t2, $t3
sw $t2, 0($t0)
la $t0, p_update_i
la $t2, p_update_i
lw $t3, 0($t2)
li $t2, 1
add $t3, $t3, $t2
sw $t3, 0($t0)
j while_2
endwhile_3:
lw $t0, p_update_value
lw $t3, 0($t0)
j proc_update_end_1
proc_update_end_1:
lw $ra, 0($sp)
addi $sp, $sp, 4
jr $ra
main:
la $t3, g_count
li $v0, 5
syscall
sw $v0, 0($t3)
la $t3, g_total
la $t0, g_count
lw $t2, 0($t0)
li $t0, 2
li $t1, 1
div $t0, $t0, $t1
mul $t2, $t2, $t0
sw $t2, 0($t3)
la $t3, g_mark
li $t2, 65
sw $t2, 0($t3)
la $t3, g_letters
li $t2, 1
addi $t2, $t2, -1
li $at, 4
mul $t2, $t2, $at
add $t3, $t3, $t2
la $t2, g_mark
lw $t0, 0($t2)
sw $t0, 0($t3)
la $t3, g_pair
la $t0, g_total
lw $t2, 0($t0)
sw $t2, 0($t3)
la $t3, g_count
lw $t2, 0($t3)
li $t3, 0
bne $t2, $t3, else_4
la $t3, g_total
lw $t2, 0($t3)
move $a0, $t2
li $v0, 1
syscall
li $a0, 10
li $v0, 11
syscall
j endif_5
else_4:
la $t2, g_total
sw $t2, p_update_value
la $t2, g_mark
lw $t3, 0($t2)
sw $t3, p_update_flag
jal proc_update
endif_5:
li $v0, 10
syscall
