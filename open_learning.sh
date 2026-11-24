#!/bin/bash

# Dreaming in AI - Learning Center
# This script opens the learning materials in your browser

echo "🧠 Dreaming in AI - Learning Center"
echo "=================================="
echo ""
echo "Opening learning materials in your browser..."
echo ""

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Open the index.html file in the default browser
if command -v xdg-open &> /dev/null; then
    # Linux
    xdg-open "$DIR/index.html"
elif command -v open &> /dev/null; then
    # macOS
    open "$DIR/index.html"
elif command -v start &> /dev/null; then
    # Windows (Git Bash/WSL)
    start "$DIR/index.html"
else
    echo "Could not open browser automatically."
    echo "Please open the following file manually:"
    echo "$DIR/index.html"
fi

echo ""
echo "📚 Available Learning Materials:"
echo "   1. index.html - Main learning center"
echo "   2. quick_reference.html - Quick reference card"
echo "   3. learning_guide.html - Complete learning guide"
echo "   4. output_explained.html - Output explanation"
echo ""
echo "💡 Recommended starting point:"
echo "   Start with quick_reference.html for a quick overview"
echo "   Then read learning_guide.html for complete understanding"
echo "   Finally, check output_explained.html to understand the code output"
echo ""
echo "🚀 Next steps:"
echo "   1. Read the learning materials"
echo "   2. Run: python3 examples/compare_architectures.py"
echo "   3. Understand the output"
echo "   4. Start experimenting with the code"
echo ""
echo "Happy learning! 🎉"