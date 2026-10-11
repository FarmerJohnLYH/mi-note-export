#include <iostream>
#include <iomanip>
#include <stdlib.h>     /* srand, rand */
#include <time.h>       /* time */
using namespace std;

/* UPLOAD START */

template <class T>
class ASet{ // for now we assume datatype T can be sorted
    private:
        int n;
        T* a;
        
    public:
    
        ASet(){
            n=0;
            
            a= new T[0];
        }
        ASet(T* arr, int p){ //incoming array is guaranteed not to have duplicates.
            n=p;
            a=new T[n];
            for(int i=0;i<n;i++){
                a[i] = arr[i];
            }
       
           
            
        }
        ~ASet(){
            
        }
       
        void addEle(T x){
            int f=1;
            for(int i=0;i<n;i++){
             	if(a[i]==x){
             	f=0;
				}
			}
			if(f){
				T * old = a;
				a= new T[n+1];
				for(int j=0;j<n;j++) a[j]=old[j];
			
            	a[n] = x;
            	n++;
			}
        }
        bool isEmpty(){
            if(n!=0) return 0;
            else return 1;
        }
        bool removeEle(T x){
            // returns true if removed.
            // returns false if it is not in the set
            for(int i=0;i<n;i++){
            	if(a[i]==x) {
            		for(int j=i;j<n-1;j++){
						a[j]=a[j+1];
					} 
					n--;
					return 1;
				}
			}
			return 0;
        }
        bool hasEle(T x){
            for(int i=0;i<n;i++){
             	if(a[i]==x){
             		return true; 
				}
			}
			return false;
        }
        bool isSubsetOf(ASet<T>* S){
            for(int k=0;k<n;k++){
        		if(!S->hasEle(a[k])){
        			return false;
				}
			}
			return true;
        }
        bool containsSet(ASet<T>* S){
            for(int i=0;i<n;i++){
            	S->removeEle(a[i]);
			}
			if(S->isEmpty())return 1;
			return 0;
        }
        bool isDisjointFrom(ASet<T>* S){
            for(int i=0;i<n;i++){ 
            	if(S->hasEle(a[i])) return 0;
			}
			return 1;
        }
        int cardinality(){
            return n;
        }
        ASet<T>* setUnion(ASet<T>* S){
			ASet<T>* U=new ASet<T>(a,n);
			for(int i=0;i<S->n;i++){
				U->addEle(S->a[i]);
			}
			 return U;
        }
        ASet<T>* intersect(ASet<T>* S){
            ASet<T>* U=new ASet<T>(a,n);
            for(int i=0;i<n;i++){
            	if(!S->hasEle(a[i])) U->removeEle(a[i]);
			}
			return U;
        }
        ASet<T>* subtract(ASet<T>* S){
            ASet<T>* U=new ASet<T>(a,n);
            for(int i=0;i<S->n;i++){
            	U->removeEle(S->a[i]);
			}
			 return U;
        }
        ASet<T>* symDiff(ASet<T>* S){
            ASet<T>* U=setUnion(S);
            ASet<T>* P=intersect(S); 
            for(int i=0;i<P->n;i++){
            	U->removeEle(P->a[i]);
			}
			return U;
        }
        void printSet(){ //print the elements of S in ascending order
            for(int i=0;i<n;i++){
            	for(int j=0;j<n-1;j++){
            		if(a[j]>a[j+1]){
            			int tmp=a[j];
            			a[j]=a[j+1];
            			a[j+1]=tmp;
					}
				}
			}
            
            	cout<<"{";
			for(int i=0;i<n-1;i++){
				cout<<a[i]<<",";
			}
			cout<<a[n-1]<<"}"<<endl;
        }
        
};

/* UPLOAD END */

void simpleTest(){
    ASet<int> A;
    for(int i=1;i<=4;i++){
        A.addEle(i);
    }
    cout << "Set A: ";
    A.printSet();
    cout << "Cardinality of A: " << A.cardinality() << endl << endl;
    
    int tmpB[4] = {3,6,5,4};
    ASet<int> B(tmpB,4);
    cout << "Set B: ";
    B.printSet();
    cout << "Cardinality of B: " << B.cardinality() << endl << endl;
    
    ASet<int>* C = A.intersect(&B);
    ASet<int>* D = A.subtract(&B);
    
    cout << "Intersecting A and B gets set C, ";
    C->printSet();
    cout << "Union of A and B is ";
    (A.setUnion(&B))->printSet();
    cout << "Let set D be the set of elements in A but not B. Then set D is ";
    D->printSet();
    cout << "Symmetric difference of A and B is ";
    (A.symDiff(&B))->printSet();
    
    cout << endl << "Checking subset relations:" << endl;
    cout << "A " << (A.containsSet(C) ? "contains" : "does not contain") << " set C" << endl;
    cout << "B " << (B.containsSet(D) ? "contains" : "does not contain") << " set D" << endl;
    cout << "C is " << (C->isSubsetOf(&A) ? "" : "not ") << "a subset of A" << endl;
    cout << "D is " << (D->isSubsetOf(&B) ? "" : "not ") << "a subset of B" << endl;
}

int main(){
    simpleTest();
    return 0;
}
