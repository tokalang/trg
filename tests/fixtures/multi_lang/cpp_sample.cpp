#include <iostream>
#include <vector>
#include <string>

template<typename T>
class MatrixBuffer {
public:
    explicit MatrixBuffer(size_t capacity) : cap_(capacity) {}

    // Regular comment here
    bool push_item(const T& item) {
        if (items_.size() >= cap_) return false;
        items_.push_back(item);
        return true;
    }

    size_t size() const { return items_.size(); }

private:
    size_t cap_;
    std::vector<T> items_;
};

int main(int argc, char** argv) {
    MatrixBuffer<int> buf(16);
    buf.push_item(42);
    std::cout << "Buffer size: " << buf.size() << std::endl;
    return 0;
}
