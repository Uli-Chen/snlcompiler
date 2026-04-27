# SNL 语法规则

本文依据 `编译原理课程设计.pptx` 第 6 页到第 16 页整理。第 6 页给出
SNL 程序结构，第 7 页到第 16 页给出 SNL 语言的上下文无关文法。

说明：

- `ε` 表示空产生式。
- `ID` 表示标识符。
- `INTC` 表示无符号整数常量。
- PPT 第 7 页的编号在 `TypeDec` 处出现跳号；这里按产生式顺序补齐为第
  5、6 条，后续编号保持与 PPT 一致。

## 程序结构

SNL 程序由以下部分组成：

1. 程序头
2. 声明部分
   - 类型声明部分
   - 变量声明部分
   - 过程声明部分
3. 程序体部分

示例结构：

```snl
program pp
var
  integer v1;
  char c;
procedure f();
begin
  v1 := 2
end
begin
  f();
  write(v1)
end.
```

## 上下文无关文法

```bnf
1. Program ::= ProgramHead DeclarePart ProgramBody

2. ProgramHead ::= PROGRAM ProgramName

3. ProgramName ::= ID

4. DeclarePart ::= TypeDec VarDec ProcDec

5. TypeDec ::= ε

6. TypeDec ::= TypeDeclaration

7. TypeDeclaration ::= TYPE TypeDecList

8. TypeDecList ::= TypeId = TypeName ; TypeDecMore

9. TypeDecMore ::= ε

10. TypeDecMore ::= TypeDecList

11. TypeId ::= ID

12. TypeName ::= BaseType

13. TypeName ::= StructureType

14. TypeName ::= ID

15. BaseType ::= INTEGER

16. BaseType ::= CHAR

17. StructureType ::= ArrayType

18. StructureType ::= RecType

19. ArrayType ::= ARRAY [ Low .. Top ] OF BaseType

20. Low ::= INTC

21. Top ::= INTC

22. RecType ::= RECORD FieldDecList END

23. FieldDecList ::= BaseType IdList ; FieldDecMore

24. FieldDecList ::= ArrayType IdList ; FieldDecMore

25. FieldDecMore ::= ε

26. FieldDecMore ::= FieldDecList

27. IdList ::= ID IdMore

28. IdMore ::= ε

29. IdMore ::= , IdList

30. VarDec ::= ε

31. VarDec ::= VarDeclaration

32. VarDeclaration ::= VAR VarDecList

33. VarDecList ::= TypeName VarIdList ; VarDecMore

34. VarDecMore ::= ε

35. VarDecMore ::= VarDecList

36. VarIdList ::= ID VarIdMore

37. VarIdMore ::= ε

38. VarIdMore ::= , VarIdList

39. ProcDec ::= ε

40. ProcDec ::= ProcDeclaration

41. ProcDeclaration ::= PROCEDURE ProcName ( ParamList ) ; ProcDecPart ProcBody ProcDecMore

42. ProcDecMore ::= ε

43. ProcDecMore ::= ProcDeclaration

44. ProcName ::= ID

45. ParamList ::= ε

46. ParamList ::= ParamDecList

47. ParamDecList ::= Param ParamMore

48. ParamMore ::= ε

49. ParamMore ::= ; ParamDecList

50. Param ::= TypeName FormList

51. Param ::= VAR TypeName FormList

52. FormList ::= ID FidMore

53. FidMore ::= ε

54. FidMore ::= , FormList

55. ProcDecPart ::= DeclarePart

56. ProcBody ::= ProgramBody

57. ProgramBody ::= BEGIN StmList END

58. StmList ::= Stm StmMore

59. StmMore ::= ε

60. StmMore ::= ; StmList

61. Stm ::= ConditionalStm

62. Stm ::= LoopStm

63. Stm ::= InputStm

64. Stm ::= OutputStm

65. Stm ::= ReturnStm

66. Stm ::= ID AssCall

67. AssCall ::= AssignmentRest

68. AssCall ::= CallStmRest

69. AssignmentRest ::= VariMore := Exp

70. ConditionalStm ::= IF RelExp THEN StmList ELSE StmList FI

71. LoopStm ::= WHILE RelExp DO StmList ENDWH

72. InputStm ::= READ ( Invar )

73. Invar ::= ID

74. OutputStm ::= WRITE ( Exp )

75. ReturnStm ::= RETURN ( Exp )

76. CallStmRest ::= ( ActParamList )

77. ActParamList ::= ε

78. ActParamList ::= Exp ActParamMore

79. ActParamMore ::= ε

80. ActParamMore ::= , ActParamList

81. RelExp ::= Exp OtherRelE

82. OtherRelE ::= CmpOp Exp

83. Exp ::= Term OtherTerm

84. OtherTerm ::= ε

85. OtherTerm ::= AddOp Exp

86. Term ::= Factor OtherFactor

87. OtherFactor ::= ε

88. OtherFactor ::= MultOp Term

89. Factor ::= ( Exp )

90. Factor ::= INTC

91. Factor ::= CHARC

92. Factor ::= Variable

93. Variable ::= ID VariMore

94. VariMore ::= ε

95. VariMore ::= [ Exp ]

96. VariMore ::= . FieldVar

97. FieldVar ::= ID FieldVarMore

98. FieldVarMore ::= ε

99. FieldVarMore ::= [ Exp ]

100. CmpOp ::= <

101. CmpOp ::= =

102. AddOp ::= +

103. AddOp ::= -

104. MultOp ::= *

105. MultOp ::= /
```
